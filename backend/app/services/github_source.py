"""GitHub source connection + read-only REST proxy (ADR-038, W2b).

Two responsibilities, both at the **connector/credential boundary** (ADR-019):

1. **Connection lifecycle** — store/rotate/soft-revoke the owner's GitHub credential
   (a fine-grained PAT with ``contents:read``, first version). The token is AEAD-sealed
   in ``github_connections`` and only ever decrypted here; it is **never** returned to a
   client, written into a project tree/snapshot/prompt/log/event, or handed to the agent.

2. **Read-only GitHub REST** — repo/ref pickers (server-side proxy) + the import
   primitives the durable worker uses: resolve a ref → a concrete commit OID and
   bounded-fetch that OID's tarball. All GitHub failures surface as **redacted** errors
   (never the upstream body, never the token).

W2b is a **one-time** import: read-only fetch ⇒ idempotent, no remote mutation, no
``effect_unknown`` reconciliation (that is W4). No ``git`` binary, no ``.git``, no working
copy, no sandbox.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import GithubConnection
from app.security import (
    FINE_GRAINED_PAT_PREFIX,
    GithubSeal,
    GithubTokenIdentity,
    classify_github_token,
    connector_vault_capability,
    load_keyring,
    open_github_token,
    seal_github_token,
)
from app.services.context import CallerContext
from app.services.errors import BadGateway, Conflict, Invalid, NotFound

logger = logging.getLogger("app.projects.github")

_API_VERSION = "2022-11-28"
_UA = "sherpa-workspace-import"
_REPO_PAGE = 30
_MAX_REPO_PAGES = 20


class GithubApiError(Exception):
    """Redacted GitHub failure. ``code`` is a stable, non-sensitive termination reason
    (never carries the upstream body or the token)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclasses.dataclass(frozen=True)
class RepoInfo:
    repo_external_id: str
    owner: str
    repo: str
    private: bool
    default_branch: str


@dataclasses.dataclass(frozen=True)
class RefInfo:
    ref_type: str  # branch | tag
    name: str
    oid: str


@dataclasses.dataclass(frozen=True)
class ConnectionStatus:
    id: uuid.UUID | None
    connected: bool
    auth_kind: str | None
    account_login: str | None
    scopes: list[str]
    status: str | None
    last_error_redacted: str | None


# --- connection lifecycle ---------------------------------------------------


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("github connection requires a user context")
    return ctx.user_id


async def get_live_connection(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID
) -> GithubConnection | None:
    """The owner's single non-revoked connection (uq_ghc_owner_active), or None."""
    return await db.scalar(
        select(GithubConnection).where(
            GithubConnection.tenant_id == ctx.tenant_id,
            GithubConnection.user_id == uid,
            GithubConnection.status != "revoked",
        )
    )


async def get_status(db: AsyncSession, ctx: CallerContext) -> ConnectionStatus:
    uid = _require_user(ctx)
    conn = await get_live_connection(db, ctx, uid)
    if conn is None:
        return ConnectionStatus(
            id=None,
            connected=False,
            auth_kind=None,
            account_login=None,
            scopes=[],
            status=None,
            last_error_redacted=None,
        )
    return ConnectionStatus(
        id=conn.id,
        connected=conn.status == "active",
        auth_kind=conn.auth_kind,
        account_login=conn.account_login,
        scopes=list(conn.scopes or []),
        status=conn.status,
        last_error_redacted=conn.last_error_redacted,
    )


def _seal_for(conn: GithubConnection) -> GithubSeal:
    return GithubSeal(
        token_enc=conn.token_enc or b"",
        nonce=conn.nonce or b"",
        kek_id=conn.kek_id or "",
        key_version=conn.key_version or 0,
        token_algorithm=conn.token_algorithm or "",
        aad_version=conn.aad_version or 0,
    )


def _open_token(conn: GithubConnection) -> str:
    """Decrypt a live connection's token at the connector boundary (never logged)."""
    identity = GithubTokenIdentity(
        tenant_id=conn.tenant_id,
        connection_id=conn.id,
        user_id=conn.user_id,
        auth_kind=conn.auth_kind,
    )
    return open_github_token(
        _seal_for(conn), identity, connector_vault_capability(), load_keyring()
    )


async def create_connection(
    db: AsyncSession, ctx: CallerContext, *, auth_kind: str, token: str
) -> GithubConnection:
    """Seal a GitHub PAT into the AEAD vault and validate it against GitHub. Any prior
    connection is soft-revoked first (one active connection per owner). Caller commits.

    v1 accepts ONLY a fine-grained PAT (``github_pat_`` prefix); classic PAT / OAuth /
    GitHub App tokens are rejected at this input boundary BEFORE any network call. The
    rejection reason is a stable category label and never echoes the submitted token.
    Validation then calls ``GET /user`` so a bad/expired token is rejected synchronously
    (422); the account login is stored for display. The plaintext token never leaves
    this function."""
    uid = _require_user(ctx)
    if auth_kind != "pat":
        raise Invalid("only fine-grained PAT connections are supported in v1")
    token = (token or "").strip()
    if not token:
        raise Invalid("token is required")
    category = classify_github_token(token)
    if category != "fine_grained_pat":
        # Reject non fine-grained credentials up front. Report only the category label —
        # never the token, its length, a fragment, or a hash.
        logger.warning("github token rejected: unsupported category", extra={"category": category})
        raise Invalid(
            f"only fine-grained PAT ({FINE_GRAINED_PAT_PREFIX}...) tokens are accepted in v1"
        )

    # Validate + learn the login BEFORE sealing, so we never persist a dead credential.
    try:
        login, scopes = await _validate_pat(token)
    except GithubApiError as exc:
        raise Invalid(f"github token rejected: {exc.code}") from None

    # Soft-revoke any prior live connection (keeps provenance FKs intact).
    prior = await get_live_connection(db, ctx, uid)
    if prior is not None:
        prior.status = "revoked"
        prior.token_enc = None
        prior.nonce = None
        prior.kek_id = None
        prior.key_version = None
        prior.token_algorithm = None
        prior.aad_version = None
        await db.flush()

    conn = GithubConnection(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        auth_kind="pat",
        account_login=login,
        scopes=scopes or ["contents:read"],
        status="pending",
    )
    db.add(conn)
    await db.flush()

    identity = GithubTokenIdentity(
        tenant_id=conn.tenant_id, connection_id=conn.id, user_id=uid, auth_kind="pat"
    )
    seal = seal_github_token(token, identity, load_keyring())
    conn.token_enc = seal.token_enc
    conn.nonce = seal.nonce
    conn.kek_id = seal.kek_id
    conn.key_version = seal.key_version
    conn.token_algorithm = seal.token_algorithm
    conn.aad_version = seal.aad_version
    conn.status = "active"
    await db.flush()
    logger.info("github connection created", extra={"account_login": login})
    return conn


async def delete_connection(db: AsyncSession, ctx: CallerContext) -> None:
    """Soft-revoke the owner's connection + wipe the sealed token. Provenance rows keep
    their (now-revoked) connection reference. 404 when there is nothing to revoke."""
    uid = _require_user(ctx)
    conn = await get_live_connection(db, ctx, uid)
    if conn is None:
        raise NotFound("no github connection")
    conn.status = "revoked"
    conn.token_enc = None
    conn.nonce = None
    conn.kek_id = None
    conn.key_version = None
    conn.token_algorithm = None
    conn.aad_version = None
    await db.flush()
    logger.info("github connection revoked")


async def _require_active_token(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID
) -> tuple[GithubConnection, str]:
    conn = await get_live_connection(db, ctx, uid)
    if conn is None or conn.status != "active" or conn.token_enc is None:
        raise Conflict("no active github connection")
    return conn, _open_token(conn)


# --- GitHub REST (redacted errors) ------------------------------------------


def _headers(token: str | None) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": _UA,
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _redact_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "auth_required"
    if status_code == 404:
        return "repo_unavailable"
    return "error"


def _make_async_client(timeout: float) -> httpx.AsyncClient:
    """Single construction point for GitHub HTTP clients (a test seam). httpx strips the
    Authorization header on cross-host redirects (tarball → codeload), so the token never
    leaves ``api.github.com``."""
    return httpx.AsyncClient(
        base_url=settings.github_api_base, timeout=timeout, follow_redirects=True
    )


async def _validate_pat(token: str) -> tuple[str, list[str]]:
    """GET /user with the PAT; returns (login, scopes). Raises GithubApiError."""
    async with _make_async_client(15.0) as client:
        try:
            resp = await client.get("/user", headers=_headers(token))
        except httpx.HTTPError as exc:  # noqa: BLE001 - normalized/redacted
            raise GithubApiError("upstream_unreachable", type(exc).__name__) from None
    if resp.status_code != 200:
        raise GithubApiError(_redact_status(resp.status_code))
    data = resp.json()
    login = str(data.get("login") or "")
    # Fine-grained PAT scopes are not exposed as a header; record the intended scope.
    return login, ["contents:read"]


async def list_repos(
    db: AsyncSession, ctx: CallerContext, *, query: str | None, cursor: str | None, limit: int
) -> tuple[list[RepoInfo], str | None]:
    """Read-only repo picker (server-side proxy through the stored credential)."""
    uid = _require_user(ctx)
    _conn, token = await _require_active_token(db, ctx, uid)
    page = 1
    if cursor:
        try:
            page = max(1, int(cursor))
        except ValueError:
            raise Invalid("bad cursor") from None
    q = (query or "").strip().lower()
    limit = max(1, min(limit, 100))

    out: list[RepoInfo] = []
    next_cursor: str | None = None
    async with _make_async_client(20.0) as client:
        scanned_pages = 0
        while len(out) < limit and scanned_pages < _MAX_REPO_PAGES:
            try:
                resp = await client.get(
                    "/user/repos",
                    headers=_headers(token),
                    params={
                        "per_page": _REPO_PAGE,
                        "page": page,
                        "sort": "updated",
                        "affiliation": "owner,collaborator,organization_member",
                    },
                )
            except httpx.HTTPError as exc:  # noqa: BLE001
                raise BadGateway(f"github unreachable: {type(exc).__name__}") from None
            if resp.status_code != 200:
                raise BadGateway(f"github error: {_redact_status(resp.status_code)}")
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                info = RepoInfo(
                    repo_external_id=str(r.get("id")),
                    owner=str((r.get("owner") or {}).get("login") or ""),
                    repo=str(r.get("name") or ""),
                    private=bool(r.get("private")),
                    default_branch=str(r.get("default_branch") or "main"),
                )
                if q and q not in f"{info.owner}/{info.repo}".lower():
                    continue
                out.append(info)
                if len(out) >= limit:
                    next_cursor = str(page + 1) if len(rows) == _REPO_PAGE else None
                    break
            scanned_pages += 1
            if len(rows) < _REPO_PAGE:
                break
            page += 1
    return out[:limit], next_cursor


async def _resolve_repo_full_name(
    client: httpx.AsyncClient, token: str, repo_external_id: str
) -> tuple[str, str]:
    try:
        resp = await client.get(f"/repositories/{repo_external_id}", headers=_headers(token))
    except httpx.HTTPError as exc:  # noqa: BLE001
        raise GithubApiError("upstream_unreachable", type(exc).__name__) from None
    if resp.status_code != 200:
        raise GithubApiError(_redact_status(resp.status_code))
    data = resp.json()
    owner = str((data.get("owner") or {}).get("login") or "")
    repo = str(data.get("name") or "")
    if not owner or not repo:
        raise GithubApiError("repo_unavailable")
    return owner, repo


async def list_refs(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    repo_external_id: str,
    kind: str | None,
    query: str | None,
) -> list[RefInfo]:
    """Read-only ref picker: branches and/or tags for a repo (by stable numeric id)."""
    uid = _require_user(ctx)
    _conn, token = await _require_active_token(db, ctx, uid)
    want = (kind or "").strip().lower()
    q = (query or "").strip().lower()
    out: list[RefInfo] = []
    async with _make_async_client(20.0) as client:
        try:
            owner, repo = await _resolve_repo_full_name(client, token, repo_external_id)
        except GithubApiError as exc:
            raise BadGateway(f"github error: {exc.code}") from None
        specs = []
        if want in ("", "branch"):
            specs.append(("branch", f"/repos/{owner}/{repo}/branches"))
        if want in ("", "tag"):
            specs.append(("tag", f"/repos/{owner}/{repo}/tags"))
        for ref_type, path in specs:
            try:
                resp = await client.get(path, headers=_headers(token), params={"per_page": 100})
            except httpx.HTTPError as exc:  # noqa: BLE001
                raise BadGateway(f"github unreachable: {type(exc).__name__}") from None
            if resp.status_code != 200:
                raise BadGateway(f"github error: {_redact_status(resp.status_code)}")
            for r in resp.json() or []:
                name = str(r.get("name") or "")
                oid = str((r.get("commit") or {}).get("sha") or "")
                if not name or not oid:
                    continue
                if q and q not in name.lower():
                    continue
                out.append(RefInfo(ref_type=ref_type, name=name, oid=oid))
    return out


# --- import primitives (used by the durable worker) --------------------------


async def resolve_ref_to_oid(
    client: httpx.AsyncClient, token: str | None, *, owner: str, repo: str, ref_type: str, ref: str
) -> str:
    """Resolve a branch/tag/commit ref to a concrete commit OID. Raises GithubApiError
    with a named, redacted code."""
    if ref_type == "commit":
        resp = await _gh_get(client, f"/repos/{owner}/{repo}/commits/{ref}", token)
        sha = str(resp.get("sha") or "")
        if not sha:
            raise GithubApiError("source_resolve_failed")
        return sha
    if ref_type == "branch":
        obj = await _gh_get(client, f"/repos/{owner}/{repo}/git/ref/heads/{ref}", token)
        return _deref_ref_object(obj)
    if ref_type == "tag":
        obj = await _gh_get(client, f"/repos/{owner}/{repo}/git/ref/tags/{ref}", token)
        target = obj.get("object")
        target_dict: dict[str, object] = target if isinstance(target, dict) else {}
        if str(target_dict.get("type")) == "tag":
            # Annotated tag → dereference to the commit it points at.
            inner = await _gh_get(
                client, f"/repos/{owner}/{repo}/git/tags/{target_dict.get('sha')}", token
            )
            inner_obj = inner.get("object")
            inner_dict: dict[str, object] = inner_obj if isinstance(inner_obj, dict) else {}
            commit = inner_dict.get("sha")
            if not commit:
                raise GithubApiError("source_resolve_failed")
            return str(commit)
        return _deref_ref_object(obj)
    raise GithubApiError("source_resolve_failed", "unknown ref type")


def _deref_ref_object(obj: dict[str, object]) -> str:
    target = obj.get("object")
    sha = target.get("sha") if isinstance(target, dict) else None
    if not sha:
        raise GithubApiError("source_resolve_failed")
    return str(sha)


async def _gh_get(client: httpx.AsyncClient, path: str, token: str | None) -> dict[str, object]:
    try:
        resp = await client.get(path, headers=_headers(token))
    except httpx.HTTPError as exc:  # noqa: BLE001
        raise GithubApiError("upstream_unreachable", type(exc).__name__) from None
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, dict):
            return data
        raise GithubApiError("source_resolve_failed")
    raise GithubApiError(_redact_status(resp.status_code))


async def fetch_repo_tarball(
    client: httpx.AsyncClient, token: str | None, *, owner: str, repo: str, oid: str, max_bytes: int
) -> bytes:
    """Bounded fetch of the ``tarball/{oid}`` archive (contents only, no git history).
    Aborts over ``max_bytes`` (→ 'too_large'). Redacted errors."""
    url = f"/repos/{owner}/{repo}/tarball/{oid}"
    chunks: list[bytes] = []
    total = 0
    try:
        async with client.stream("GET", url, headers=_headers(token)) as resp:
            if resp.status_code >= 400:
                raise GithubApiError(_redact_status(resp.status_code))
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise GithubApiError("too_large")
                chunks.append(chunk)
    except httpx.HTTPError as exc:  # noqa: BLE001
        raise GithubApiError("upstream_unreachable", type(exc).__name__) from None
    if total == 0:
        raise GithubApiError("repo_unavailable")
    return b"".join(chunks)


def open_connection_token_for_worker(conn: GithubConnection) -> str | None:
    """Decrypt a connection's token for the durable worker at the connector boundary.
    Returns None for a revoked/tokenless connection (public-repo path is a later
    relaxation; W2b requires an active connection)."""
    if conn.status != "active" or conn.token_enc is None:
        return None
    return _open_token(conn)


def make_client() -> httpx.AsyncClient:
    """httpx client for GitHub import calls (auth stripped on cross-host redirects to
    codeload by httpx's default redirect handling — the token never leaves api.github.com)."""
    return _make_async_client(float(settings.github_archive_timeout_seconds))

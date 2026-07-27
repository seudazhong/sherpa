"""Deterministic GitHub API mock for W2b import tests (ADR-038).

Builds an ``httpx.MockTransport`` handler that answers the GitHub REST endpoints the
W2b import path touches (GET /user, /user/repos, /repositories/{id}, branches/tags,
git/ref resolve, commits, tarball). No network, no real token. Patch
``github_source._make_async_client`` with :meth:`GithubMock.client_factory` so every
GitHub call (connection validate, pickers, worker resolve+fetch) routes through it.
"""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Callable

import httpx

from app.config import settings

TEST_OID = "a" * 40


def make_tarball(root: str, members: list[tuple[str, bytes]]) -> bytes:
    """A gzip tar shaped like a GitHub archive: everything under a single ``root/`` dir."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        di = tarfile.TarInfo(root + "/")
        di.type = tarfile.DIRTYPE
        tf.addfile(di)
        for name, data in members:
            ti = tarfile.TarInfo(f"{root}/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def make_unsafe_tarball() -> bytes:
    """A gzip tar with a path-traversal member (rejected by the safe expander)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"pwn"
        ti = tarfile.TarInfo("../escape.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class GithubMock:
    def __init__(
        self,
        *,
        login: str = "octocat",
        owner: str = "octocat",
        repo: str = "hello",
        repo_id: str = "123",
        oid: str = TEST_OID,
        tarball: bytes | None = None,
        fail_tarball_times: int = 0,
        fail_resolve_times: int = 0,
        user_status: int = 200,
    ) -> None:
        self.login = login
        self.owner = owner
        self.repo = repo
        self.repo_id = repo_id
        self.oid = oid
        self.tarball = (
            tarball
            if tarball is not None
            else make_tarball(
                f"{owner}-{repo}-{oid[:7]}", [("README.md", b"# hello\n"), ("src/app.py", b"x=1\n")]
            )
        )
        self.fail_tarball_times = fail_tarball_times
        self.fail_resolve_times = fail_resolve_times
        self.user_status = user_status
        self.tarball_calls = 0
        self.resolve_calls = 0
        self.seen_auth: list[str | None] = []

    def _json(self, obj: object, status: int = 200) -> httpx.Response:
        return httpx.Response(
            status, content=json.dumps(obj).encode(), headers={"content-type": "application/json"}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen_auth.append(request.headers.get("authorization"))
        if path == "/user":
            if self.user_status != 200:
                return self._json({"message": "bad"}, self.user_status)
            return self._json({"login": self.login})
        if path == "/user/repos":
            return self._json(
                [
                    {
                        "id": int(self.repo_id),
                        "name": self.repo,
                        "owner": {"login": self.owner},
                        "private": True,
                        "default_branch": "main",
                    }
                ]
            )
        if path == f"/repositories/{self.repo_id}":
            return self._json({"name": self.repo, "owner": {"login": self.owner}})
        if path == f"/repos/{self.owner}/{self.repo}/branches":
            return self._json([{"name": "main", "commit": {"sha": self.oid}}])
        if path == f"/repos/{self.owner}/{self.repo}/tags":
            return self._json([{"name": "v1.0", "commit": {"sha": self.oid}}])
        if path.startswith(f"/repos/{self.owner}/{self.repo}/git/ref/heads/"):
            self.resolve_calls += 1
            if self.resolve_calls <= self.fail_resolve_times:
                return self._json({"message": "boom"}, 500)
            return self._json({"object": {"sha": self.oid, "type": "commit"}})
        if path.startswith(f"/repos/{self.owner}/{self.repo}/git/ref/tags/"):
            return self._json({"object": {"sha": self.oid, "type": "commit"}})
        if path.startswith(f"/repos/{self.owner}/{self.repo}/commits/"):
            return self._json({"sha": self.oid})
        if path.startswith(f"/repos/{self.owner}/{self.repo}/tarball/"):
            self.tarball_calls += 1
            if self.tarball_calls <= self.fail_tarball_times:
                return httpx.Response(500, content=b"boom")
            return httpx.Response(200, content=self.tarball)
        return httpx.Response(404, content=b'{"message":"not found"}')

    def client_factory(self) -> Callable[[float], httpx.AsyncClient]:
        def factory(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(self.handler),
                base_url=settings.github_api_base,
                follow_redirects=True,
            )

        return factory

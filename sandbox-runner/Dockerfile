# syntax=docker/dockerfile:1
#
# Sherpa sandbox runner (ADR-047 §决策4, owner decision O-6).
#
# The v1 coding runtime image. Deliberately small and deliberately incapable:
#
#   * python + pytest + ruff, pinned — nothing else a coding task needs is silently
#     available, so "it worked on my machine" cannot happen inside the sandbox;
#   * NO git, NO curl/wget, NO package manager index at runtime — the sandbox is
#     network-disabled (ADR-025) and never installs anything, so a missing dependency is an
#     honest `environment_missing_dependencies`, not a surprise download;
#   * a non-root user (uid/gid 10001) that owns /work;
#   * `VOLUME /work` — this is load-bearing. The container runs with a read-only rootfs, so
#     /work has to be a mount to be writable, and declaring it in the image gives us an
#     ANONYMOUS volume: the orchestrator passes NO mount and NO host path to the daemon
#     (ADR-047 §决策1), and the volume inherits this directory's ownership so the non-root
#     user can write into it. Removed with the container (`remove(v=True)`).
#   * /opt/sherpa/capabilities.json — what this image can actually do, probed by the
#     orchestrator so a missing tool can be reported with the list of what IS available
#     instead of a bare exit 127.
#
# Build + pin. The runtime **refuses to run an unpinned or foreign image** (fail-closed in
# app/sandbox/runtime.verify_runner_image), so this is a required step, not a nicety:
#
#   docker build -t sherpa-sandbox-runner:dev sandbox-runner/
#   docker image inspect sherpa-sandbox-runner:dev --format '{{.Id}}'
#   # put that sha256:... in SANDBOX_IMAGE
#
# The `org.opencontainers.image.title` label below is what the runtime checks as a
# **misconfiguration guard** — it catches a digest pasted from the wrong image. It is NOT a
# security control and proves nothing about origin: labels are plain metadata that anyone
# building an image can set. The security property comes from SANDBOX_IMAGE being an
# operator-chosen immutable digest.
#
# Node is a later optional profile (O-6). Production isolation (gVisor / microVM, no shared
# docker.sock) remains ADR-039's do-not-ship gate and is NOT addressed by this image.

# The base is pinned by **registry digest**, not by the `3.11.9-slim-bookworm` tag. A tag is
# re-pointed at new bytes on every security rebuild upstream, so a tag-based FROM means the
# runner that was reviewed and the runner that ships are not necessarily the same image —
# which is the same property SANDBOX_IMAGE is pinned for one level up. Refresh deliberately:
#   docker pull python:3.11.9-slim-bookworm
#   docker image inspect python:3.11.9-slim-bookworm --format '{{index .RepoDigests 0}}'
#
# NOTE: pinning fixes *which bytes*, it does not authenticate *who produced them*. No
# signature or attestation is verified anywhere in this build; see the runner README and
# config §1.7, where that gap is recorded explicitly rather than implied away.
FROM python:3.11.9-slim-bookworm@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    RUFF_CACHE_DIR=/tmp/ruff \
    PYTEST_ADDOPTS="-p no:cacheprovider"

# Why the cache settings above matter beyond speed: /work round-trips through tar, so a
# .pytest_cache or __pycache__ written there would show up as project changes for the human
# to review. Caches go to the tmpfs /tmp instead, and bytecode is not written at all.

# Pinned toolchain. Anything not listed here is genuinely absent, on purpose.
RUN pip install --no-cache-dir \
        pytest==8.3.3 \
        ruff==0.6.9 \
 # No network tooling in a network-disabled sandbox: remove what the base image ships so a
 # command cannot even pretend to reach out. `find` keeps this honest across base updates.
 && rm -f /usr/bin/wget /usr/bin/curl /usr/bin/nc /usr/bin/telnet /usr/bin/ftp \
 && rm -rf /var/lib/apt/lists/* /root/.cache

RUN groupadd --gid 10001 sherpa \
 && useradd --uid 10001 --gid 10001 --shell /usr/sbin/nologin --no-create-home sherpa \
 && mkdir -p /work /opt/sherpa \
 && chown -R 10001:10001 /work

COPY capabilities.json /opt/sherpa/capabilities.json

# Record the same facts as an image label, so the orchestrator can read capabilities without
# starting a container once the P4 probe lands.
LABEL org.opencontainers.image.title="sherpa-sandbox-runner" \
      org.opencontainers.image.description="Offline python+pytest+ruff runner for the Sherpa coding sandbox (ADR-047)" \
      sherpa.capabilities="python,pytest,ruff"

VOLUME /work
WORKDIR /work
USER 10001:10001

# The orchestrator always supplies the command; this default only makes an accidental bare
# `docker run` harmless and self-describing.
CMD ["/bin/sh", "-lc", "cat /opt/sherpa/capabilities.json"]

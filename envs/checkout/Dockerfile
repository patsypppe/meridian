# The checkout environment snapshot.
#
# Referenced everywhere by digest, never by tag. Built for a single platform;
# CI rebuilds it and rewrites the pinned digest into the suite before running,
# because an image ID is architecture-specific and the laptop is arm64 while the
# runner is amd64 (HANDOFF §16, gotcha 1).
FROM python:3.13-slim

# Deterministic layer: no pip cache, no bytecode, versions pinned so two builds
# of the same Dockerfile produce the same image ID.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        "langgraph==0.2.76" \
        "langchain-core==0.3.34" \
        "httpx==0.28.1" \
        "pydantic==2.10.6" \
    && find / -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# The seed is baked in and copied into the workdir at trial start, so every
# trial gets a byte-identical starting state from a read-only layer.
COPY seed/ /opt/checkout/seed/
RUN python3 /opt/checkout/seed/seed_db.py \
    && rm /opt/checkout/seed/seed_db.py \
    && chmod -R a+rX /opt/checkout/seed

COPY bootstrap.sh /opt/meridian/bootstrap.sh
RUN chmod 0555 /opt/meridian/bootstrap.sh

# Never root. The runner also drops all capabilities and sets no-new-privileges;
# this is defence in depth, not the only line of defence.
RUN groupadd --gid 10001 trial && useradd --uid 10001 --gid 10001 --no-create-home trial

# The workdir is backed by a per-trial volume, and Docker seeds a fresh volume
# from this path in the image — including its ownership. Without this the volume
# arrives root-owned and the unprivileged trial user cannot write to its own
# working directory.
RUN mkdir -p /work && chown 10001:10001 /work && chmod 0775 /work

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/opt/meridian/bootstrap.sh"]

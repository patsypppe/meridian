# The model proxy image.
#
# Built with the repository root as context because it ships a slice of the
# harness. Deliberately minimal: this container holds the only provider
# credential in the system, so the less it contains the better.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir \
        "starlette==0.45.3" \
        "uvicorn==0.34.0" \
        "httpx==0.28.1" \
        "pydantic==2.10.6" \
        "pyyaml==6.0.2"

COPY src/meridian/__init__.py            /opt/meridian/meridian/__init__.py
COPY src/meridian/version.py             /opt/meridian/meridian/version.py
COPY src/meridian/hashing.py             /opt/meridian/meridian/hashing.py
COPY src/meridian/config.py              /opt/meridian/meridian/config.py
COPY src/meridian/runtime/__init__.py    /opt/meridian/meridian/runtime/__init__.py
COPY src/meridian/runtime/secrets.py     /opt/meridian/meridian/runtime/secrets.py
COPY src/meridian/runtime/proxy/         /opt/meridian/meridian/runtime/proxy/

ENV PYTHONPATH=/opt/meridian \
    MERIDIAN_CASSETTE_DIR=/cassettes \
    MERIDIAN_PROXY_PORT=8080

# Named `meridian`, not `proxy`: Debian base images already ship a `proxy` user
# and groupadd exits 9 on the collision.
RUN groupadd --gid 10002 meridian && useradd --uid 10002 --gid 10002 --no-create-home meridian \
    && mkdir -p /cassettes && chown 10002:10002 /cassettes
USER 10002:10002

EXPOSE 8080
ENTRYPOINT ["python3", "-m", "meridian.runtime.proxy.server"]

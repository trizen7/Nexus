FROM python:3.11-slim-bullseye AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0

WORKDIR /build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends binutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-fnos.txt ./
RUN python -m pip install --requirement requirements-fnos.txt

COPY nexus-gateway-fnos.spec ./
COPY nexus_gateway ./nexus_gateway

RUN python -m PyInstaller --clean --noconfirm nexus-gateway-fnos.spec \
    && test -x dist/nexus-gateway/nexus-gateway \
    && test -f dist/nexus-gateway/_internal/nexus_gateway/web/index.html \
    && test -f dist/nexus-gateway/_internal/nexus_gateway/web/app.js \
    && test -f dist/nexus-gateway/_internal/nexus_gateway/web/styles.css \
    && mkdir -p /runtime \
    && cp -aL dist/nexus-gateway /runtime/nexus-gateway \
    && cp /etc/ssl/certs/ca-certificates.crt /runtime/ca-certificates.crt \
    && test -z "$(find /runtime -type l -print -quit)" \
    && /runtime/nexus-gateway/nexus-gateway --help >/dev/null

FROM scratch AS runtime
COPY --from=builder /runtime/ /

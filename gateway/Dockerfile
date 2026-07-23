FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEXUS_TLS_DIR=/data/tls

WORKDIR /app

RUN addgroup --system --gid 10001 nexus \
    && adduser --system --uid 10001 --ingroup nexus --home /app nexus

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY start_gateway.py ./
COPY nexus_gateway ./nexus_gateway

RUN mkdir -p /data/media /data/tls && chown -R nexus:nexus /app /data

USER nexus

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import ssl,urllib.request; urllib.request.urlopen('https://127.0.0.1:8787/health', context=ssl._create_unverified_context(), timeout=3).read()" || exit 1

CMD ["python", "-m", "nexus_gateway", "--host", "0.0.0.0", "--port", "8787", "--tls-dir", "/data/tls"]

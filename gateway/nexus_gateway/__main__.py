from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from aiohttp import web

from .app import create_app
from .tls import TLSConfigurationError, TLSManager

ACCESS_LOG_FORMAT = '%a "%r" %s %b %Tf'


def _optional(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def _default_port() -> int:
    value = os.getenv("NEXUS_GATEWAY_PORT", "").strip() or "8787"
    return int(value)


def _tls_hosts() -> list[str]:
    raw = os.getenv("NEXUS_TLS_HOSTS", "")
    return [value.strip() for value in raw.replace(";", ",").split(",") if value.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nexus mobile gateway (HTTPS only)")
    parser.add_argument("--host", default=os.getenv("NEXUS_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_default_port(), help="HTTPS listener port")
    parser.add_argument("--tls-dir", default=os.getenv("NEXUS_TLS_DIR", "./data/tls"))
    return parser


async def _serve(
    app: web.Application,
    *,
    host: str,
    port: int,
    tls_manager: TLSManager,
) -> None:
    runner = web.AppRunner(app, access_log_format=ACCESS_LOG_FORMAT)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port, ssl_context=tls_manager.ssl_context)
        await site.start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.port <= 0 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")

    try:
        tls_manager = TLSManager(
            Path(args.tls_dir),
            bind_host=args.host,
            extra_hosts=_tls_hosts(),
        ).bootstrap()
    except TLSConfigurationError as exc:
        parser.error(str(exc))

    app = create_app(
        username=_optional("NEXUS_USERNAME"),
        password=_optional("NEXUS_PASSWORD"),
        session_secret=_optional("NEXUS_SESSION_SECRET"),
        upstream_url=_optional("HERMES_API_URL"),
        upstream_token=_optional("HERMES_API_TOKEN"),
        storage_dir=Path(os.getenv("NEXUS_MEDIA_DIR", "./data/media")),
        credentials_path=Path(os.getenv("NEXUS_CREDENTIALS_FILE", "./data/account.json")),
        config_path=Path(os.getenv("NEXUS_CONFIG_FILE", "./data/config.json")),
        bootstrap_token_path=Path(os.getenv("NEXUS_BOOTSTRAP_TOKEN_FILE", "./data/bootstrap.token")),
        max_upload_bytes=int(os.getenv("NEXUS_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))),
        max_total_storage_bytes=int(os.getenv("NEXUS_MAX_TOTAL_STORAGE_BYTES", str(10 * 1024 * 1024 * 1024))),
        min_free_disk_bytes=int(os.getenv("NEXUS_MIN_FREE_DISK_BYTES", str(512 * 1024 * 1024))),
        login_rate_limit=int(os.getenv("NEXUS_LOGIN_RATE_LIMIT", "5")),
        login_rate_window_seconds=float(os.getenv("NEXUS_LOGIN_RATE_WINDOW_SECONDS", "60")),
        https_port=args.port,
        tls_manager=tls_manager,
    )

    print(f"Listening on https://{args.host}:{args.port}", flush=True)
    print(f"TLS mode: {tls_manager.mode}", flush=True)
    if tls_manager.mode == "temporary":
        print(f"Local CA certificate: {tls_manager.ca_certificate_path}", flush=True)
    try:
        asyncio.run(_serve(app, host=args.host, port=args.port, tls_manager=tls_manager))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

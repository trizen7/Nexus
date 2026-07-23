from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from aiohttp import web

from .app import create_app

ACCESS_LOG_FORMAT = '%a "%r" %s %b %Tf'


def _optional(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def _default_port() -> int:
    value = os.getenv("NEXUS_GATEWAY_PORT", "").strip() or "8787"
    return int(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nexus mobile gateway (HTTP origin)")
    parser.add_argument("--host", default=os.getenv("NEXUS_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_default_port(), help="HTTP listener port")
    return parser


async def _serve(app: web.Application, *, host: str, port: int) -> None:
    runner = web.AppRunner(app, access_log_format=ACCESS_LOG_FORMAT)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.port <= 0 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")

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
    )

    print(f"Listening on http://{args.host}:{args.port}", flush=True)
    try:
        asyncio.run(_serve(app, host=args.host, port=args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

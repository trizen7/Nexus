from __future__ import annotations

import argparse
import os
from pathlib import Path

from aiohttp import web

from .app import create_app


def _optional(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexus 移动网关")
    parser.add_argument("--host", default=os.getenv("NEXUS_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NEXUS_GATEWAY_PORT", "8787")))
    args = parser.parse_args()

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
    web.run_app(app, host=args.host, port=args.port, access_log_format='%a "%r" %s %b %Tf')


if __name__ == "__main__":
    main()

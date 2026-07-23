from __future__ import annotations

import argparse
import asyncio
import os
import ssl
from pathlib import Path

from aiohttp import web

from .app import create_app

ACCESS_LOG_FORMAT = '%a "%r" %s %b %Tf'


def _optional(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nexus mobile gateway")
    parser.add_argument("--host", default=os.getenv("NEXUS_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NEXUS_GATEWAY_PORT", "8787")))
    parser.add_argument("--https-port", type=int, default=int(os.getenv("NEXUS_HTTPS_PORT", "0") or "0"))
    parser.add_argument("--tls-cert-file", default=_optional("NEXUS_TLS_CERT_FILE"))
    parser.add_argument("--tls-key-file", default=_optional("NEXUS_TLS_KEY_FILE"))
    parser.add_argument("--tls-ca-file", default=_optional("NEXUS_TLS_CA_FILE"))
    parser.add_argument(
        "--redirect-web-to-https",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("NEXUS_REDIRECT_WEB_TO_HTTPS", False),
    )
    return parser


def _existing_file(parser: argparse.ArgumentParser, value: str | None, label: str) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        parser.error(f"{label} does not exist: {path}")
    return path


def _tls_context(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[ssl.SSLContext | None, Path | None]:
    cert_path = _existing_file(parser, args.tls_cert_file, "TLS certificate")
    key_path = _existing_file(parser, args.tls_key_file, "TLS private key")
    ca_path = _existing_file(parser, args.tls_ca_file, "TLS CA certificate")
    if bool(cert_path) != bool(key_path):
        parser.error("--tls-cert-file and --tls-key-file must be configured together")
    if cert_path is None:
        if args.https_port:
            parser.error("--https-port requires a TLS certificate and private key")
        if args.redirect_web_to_https:
            parser.error("--redirect-web-to-https requires HTTPS to be enabled")
        return None, ca_path
    if args.https_port <= 0 or args.https_port > 65535:
        parser.error("--https-port must be between 1 and 65535 when TLS is enabled")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return context, ca_path


async def _serve(
    app: web.Application,
    *,
    host: str,
    http_port: int,
    https_port: int,
    ssl_context: ssl.SSLContext | None,
) -> None:
    runner = web.AppRunner(app, access_log_format=ACCESS_LOG_FORMAT)
    await runner.setup()
    try:
        http_site = web.TCPSite(runner, host=host, port=http_port)
        await http_site.start()
        if ssl_context is not None:
            https_site = web.TCPSite(runner, host=host, port=https_port, ssl_context=ssl_context)
            await https_site.start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.port <= 0 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    ssl_context, ca_path = _tls_context(parser, args)
    https_port = args.https_port if ssl_context is not None else 0

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
        https_port=https_port,
        tls_ca_path=ca_path,
        redirect_web_to_https=args.redirect_web_to_https,
    )

    print(f"Listening on http://{args.host}:{args.port}", flush=True)
    if ssl_context is not None:
        print(f"Listening on https://{args.host}:{https_port}", flush=True)
    try:
        asyncio.run(
            _serve(
                app,
                host=args.host,
                http_port=args.port,
                https_port=https_port,
                ssl_context=ssl_context,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

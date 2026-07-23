from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def load_env() -> None:
    if not ENV_FILE.is_file():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> None:
    os.chdir(BASE_DIR)
    sys.path.insert(0, str(BASE_DIR))
    load_env()
    host = os.environ.get("NEXUS_GATEWAY_HOST", "127.0.0.1")
    port = os.environ.get("NEXUS_GATEWAY_PORT", "").strip() or "8787"
    print("Starting Nexus mobile gateway (HTTPS only)...", flush=True)
    print(f"Listening on https://{host}:{port}", flush=True)
    runpy.run_module("nexus_gateway", run_name="__main__")


if __name__ == "__main__":
    main()

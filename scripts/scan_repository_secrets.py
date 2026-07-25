#!/usr/bin/env python3
"""Scan the current repository and reachable Git history without printing secret values."""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 5_000_000

SUSPICIOUS_PATHS = (
    re.compile(r"(^|/)(\.env($|\.)|account\.json$|config\.json$|process\.json$|deployment\.json$)", re.I),
    re.compile(r"\.(jks|keystore|p12|pfx|pem|key)$", re.I),
)

CONTENT_RULES = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("openai-style-key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer-token", re.compile(rb"(?i)authorization\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._~+/-]{16,}")),
)

CREDENTIAL_LITERAL = re.compile(
    rb"(?im)^\s*[\"']?(?:api[_-]?key|token|secret|password|session[_-]?secret|bootstrap[_-]?token)"
    rb"[\"']?\s*[:=]\s*[\"']([^\"'\r\n]{16,})[\"']"
)

PLACEHOLDER_MARKERS = (
    b"test",
    b"example",
    b"your-",
    b"your_",
    b"change-me",
    b"placeholder",
    b"dummy",
    b"replace-me",
    b"local-only",
)


@dataclass(frozen=True)
class Finding:
    source: str
    path: str
    rule: str


def run_git(*args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(
        ["git", *args],
        cwd=REPO_ROOT,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
    )


def entropy(value: bytes) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def is_probable_secret(value: bytes) -> bool:
    normalized = value.strip().lower()
    if any(marker in normalized for marker in PLACEHOLDER_MARKERS):
        return False
    if b" " in normalized or b"${" in normalized or normalized.startswith((b"<", b"env.")):
        return False
    return len(normalized) >= 20 and entropy(normalized) >= 4.0


def scan_blob(source: str, path: str, data: bytes) -> list[Finding]:
    findings: list[Finding] = []
    normalized_path = path.replace("\\", "/")
    if normalized_path.endswith(".env.example"):
        path_allowed = True
    else:
        path_allowed = False

    if not path_allowed:
        for pattern in SUSPICIOUS_PATHS:
            if pattern.search(normalized_path):
                findings.append(Finding(source, normalized_path, "suspicious-filename"))
                break

    if len(data) > MAX_BLOB_BYTES:
        findings.append(Finding(source, normalized_path, "large-repository-blob"))
        return findings
    if b"\x00" in data[:8192]:
        return findings

    for rule_name, pattern in CONTENT_RULES:
        if pattern.search(data):
            findings.append(Finding(source, normalized_path, rule_name))

    for match in CREDENTIAL_LITERAL.finditer(data):
        if is_probable_secret(match.group(1)):
            findings.append(Finding(source, normalized_path, "high-entropy-credential-literal"))
            break
    return findings


def history_blobs() -> Iterator[tuple[str, str, bytes]]:
    raw = run_git("rev-list", "--objects", "--all")
    paths: dict[str, str] = {}
    order: list[str] = []
    for line in str(raw).splitlines():
        object_id, *rest = line.split(" ", 1)
        if object_id not in paths:
            order.append(object_id)
            paths[object_id] = rest[0] if rest else "<unpathed>"

    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for object_id in order:
            process.stdin.write((object_id + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline().decode("utf-8", "replace").strip().split()
            if len(header) < 3:
                continue
            object_type = header[1]
            size = int(header[2])
            data = process.stdout.read(size)
            process.stdout.read(1)
            if object_type == "blob":
                yield f"history:{object_id[:12]}", paths[object_id], data
    finally:
        process.stdin.close()
        process.wait(timeout=10)


def worktree_blobs() -> Iterator[tuple[str, str, bytes]]:
    raw = run_git("ls-files", "-co", "--exclude-standard")
    for relative in str(raw).splitlines():
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        yield "worktree", relative, data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-only", action="store_true")
    parser.add_argument("--worktree-only", action="store_true")
    args = parser.parse_args()
    if args.history_only and args.worktree_only:
        parser.error("choose at most one scan scope")

    sources: list[Iterable[tuple[str, str, bytes]]] = []
    if not args.worktree_only:
        sources.append(history_blobs())
    if not args.history_only:
        sources.append(worktree_blobs())

    findings: set[Finding] = set()
    scanned = 0
    for source in sources:
        for label, path, data in source:
            scanned += 1
            findings.update(scan_blob(label, path, data))

    ordered = sorted(findings, key=lambda item: (item.path, item.rule, item.source))
    print(f"scanned_blobs={scanned}")
    print(f"findings={len(ordered)}")
    for finding in ordered:
        print(f"{finding.rule}\t{finding.source}\t{finding.path}")
    return 1 if ordered else 0


if __name__ == "__main__":
    sys.exit(main())

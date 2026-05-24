"""Notify Bing / Yandex about newly committed Markdown URLs via IndexNow.

Reads the most recent commit's added/modified files under breakdowns/ and
submits the corresponding github.com URLs.
"""

import os
import subprocess
import sys

import httpx


INDEXNOW_HOST = "api.indexnow.org"
REPO = "shixinzhang/tiktok-viral-hooks"


def _changed_breakdown_files() -> list[str]:
    """Files under breakdowns/ added or modified in HEAD."""
    out = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [
        p.strip() for p in out.splitlines()
        if p.startswith("breakdowns/") and p.endswith(".md")
    ]


def main() -> int:
    key = os.getenv("INDEXNOW_KEY", "")
    if not key:
        print("INDEXNOW_KEY missing, skipping ping", file=sys.stderr)
        return 0

    files = _changed_breakdown_files()
    if not files:
        print("No breakdown files changed in HEAD, nothing to ping")
        return 0

    urls = [f"https://github.com/{REPO}/blob/main/{p}" for p in files]
    payload = {
        "host": "github.com",
        "key": key,
        "urlList": urls,
    }
    r = httpx.post(f"https://{INDEXNOW_HOST}/indexnow", json=payload, timeout=20)
    print(f"IndexNow → {r.status_code}: {len(urls)} urls submitted")
    return 0 if r.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())

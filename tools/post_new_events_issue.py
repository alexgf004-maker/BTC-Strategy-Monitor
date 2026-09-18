from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_EVENTS = ROOT / "runtime" / "new_events.md"


def main() -> int:
    body = NEW_EVENTS.read_text(encoding="utf-8")
    title = body.splitlines()[0].lstrip("# ").strip()
    payload = json.dumps({"title": title, "body": body, "labels": ["paper-signal"]}).encode("utf-8")
    url = f"https://api.github.com/repos/{os.environ['GH_REPOSITORY']}/issues"
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        # Best-effort secondary notification; Telegram already carried the signal.
        print(f"Issue creation failed (non-fatal): {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

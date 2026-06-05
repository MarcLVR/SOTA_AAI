#!/usr/bin/env python3
"""
test_pipeline.py — tests the partial pipeline:
  connector.py  →  mini_critic (regex PII)  →  db.py  →  notifier.py  →  Telegram

Run:  python3 scripts/test_pipeline.py [doc_path]
"""

import os
import sys
from pathlib import Path
pass  # env loaded manually below

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "chase"))
sys.path.insert(0, str(_REPO_ROOT / "agents"))

# ---------------------------------------------------------------------------
# Load env — .env then optional credentials file
# ---------------------------------------------------------------------------

env_path = _REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Optional credentials file override (project-local, not committed)
creds_path = Path(os.getenv("AUDIT_CREDENTIALS", str(Path.home() / ".audit/credentials")))
if creds_path.exists():
    for line in creds_path.read_text().splitlines():
        if line.startswith("export "):
            line = line[7:]
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
                os.environ[k] = v

# ---------------------------------------------------------------------------
# Mini-critic — imported from domain/pipeline.py (single source of truth)
# ---------------------------------------------------------------------------

from domain.pipeline import mini_critic  # noqa: E402


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run(doc_path: str):
    from agents.connector import load_document
    from chase.db       import create_run
    from chase.notifier import send_finding

    print(f"\n{'='*55}")
    print(f"  Pipeline Test")
    print(f"{'='*55}")

    # Step 1 — Connector
    print(f"\n[1/4] Connector: parsing {Path(doc_path).name} ...")
    doc = load_document(doc_path)
    if doc["error"]:
        print(f"  ERROR: {doc['error']}")
        sys.exit(1)
    print(f"  Format : {doc['format'].upper()}")
    print(f"  Pages  : {doc['metadata'].get('pages') or doc['metadata'].get('slide_count') or '—'}")
    print(f"  Sections: {len(doc['sections'])}   Tables: {len(doc['tables'])}   Chars: {len(doc['text'])}")

    # Step 2 — Mini-critic
    print(f"\n[2/4] Mini-critic: scanning for PII and violations ...")
    findings = mini_critic(doc)
    if not findings:
        print("  No findings — document looks clean.")
        return
    for i, f in enumerate(findings, 1):
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["severity"], "🟡")
        print(f"  {icon} Finding {i}: {f['title']}")
        print(f"       Location  : {f['location']}")
        print(f"       Suggestion: {f['suggestion']}")

    owner = os.getenv("DEFAULT_OWNER", "admin")

    # Step 3 — DB
    print(f"\n[3/4] DB: creating validation run ...")
    try:
        run_id = create_run(doc["filename"], owner, findings)
        print(f"  run_id = {run_id}  ({len(findings)} findings saved)")
    except Exception as e:
        print(f"  DB error: {e}")
        run_id = None

    # Step 4 — Telegram
    print(f"\n[4/4] Telegram: sending notification to {owner!r} ...")
    ok = send_finding(owner, doc["filename"], findings, run_id=run_id)
    if ok:
        print(f"  Notification sent ✓  (run #{run_id})")
        print(f"\n  Check your Telegram — you should receive the message with action buttons.")
    else:
        print(f"  ERROR sending. Check TELEGRAM_BOT_TOKEN and TELEGRAM_<OWNER>_CHAT_ID.")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    doc = sys.argv[1] if len(sys.argv) > 1 else str(_REPO_ROOT / "test_docs/contrato_juan_garcia_2024.docx")
    run(doc)

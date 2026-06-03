import json
import os
import threading
import time
from pathlib import Path

import requests

FINDINGS_CACHE = Path(__file__).resolve().parent / "findings_cache.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _chat_id(env_var: str) -> int:
    val = os.getenv(env_var, "").strip()
    return int(val) if val else 0

OWNER_MAP: dict[str, int] = {
    "luca":    _chat_id("TELEGRAM_LUCA_CHAT_ID"),
    "nacho":   _chat_id("TELEGRAM_NACHO_CHAT_ID"),
    "marc":    _chat_id("TELEGRAM_MARC_CHAT_ID"),
    "augusto": _chat_id("TELEGRAM_AUGUSTO_CHAT_ID"),
}

SEVERITY_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def send_finding(owner: str, document: str, findings: list[dict], run_id: int | None = None) -> bool:
    """
    Send a validation finding to the document owner via Telegram.

    findings: list of dicts with keys: title, location, suggestion, severity
    run_id:   if None, a new run is created in DB automatically
    """
    from db import create_run
    if run_id is None:
        run_id = create_run(document, owner, findings)
    chat_id = OWNER_MAP.get(owner)
    if not chat_id:
        print(f"[notifier] Unknown owner or missing chat_id: {owner}")
        return False

    lines = [f"📄 *Documento · revisión*\n`{document}`\n"]
    for f in findings:
        icon = SEVERITY_ICON.get(f.get("severity", "medium"), "🟡")
        lines.append(f"{icon} *{f['title']}*")
        lines.append(f"📍 {f['location']}")
        lines.append(f"💡 _{f['suggestion']}_\n")

    text = "\n".join(lines)

    _save_cache(run_id, document, findings)

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Que DAVE lo corrija", "callback_data": f"fix:{run_id}"},
                {"text": "✏️ Lo corrijo yo",        "callback_data": f"manual:{run_id}"},
            ],
            [
                {"text": "🚫 Ignorar",              "callback_data": f"ignore:{run_id}"},
                {"text": "ℹ️ Más información",       "callback_data": f"info:{run_id}"},
            ],
        ]
    }

    r = requests.post(
        f"{TG_API}/sendMessage",
        json={
            "chat_id":      chat_id,
            "text":         text,
            "parse_mode":   "Markdown",
            "reply_markup": keyboard,
        },
        timeout=10,
    )

    if not r.ok:
        print(f"[notifier] Telegram error {r.status_code}: {r.text}")
    return r.ok


def _save_cache(run_id: int, document: str, findings: list[dict]) -> None:
    cache = {}
    if FINDINGS_CACHE.exists():
        try:
            cache = json.loads(FINDINGS_CACHE.read_text())
        except Exception:
            pass
    cache[str(run_id)] = {"document": document, "findings": findings}
    FINDINGS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def load_cache(run_id: int) -> dict | None:
    if not FINDINGS_CACHE.exists():
        return None
    try:
        cache = json.loads(FINDINGS_CACHE.read_text())
        return cache.get(str(run_id))
    except Exception:
        return None


def _poll_once() -> None:
    """Single poll cycle: notify owners for every unnotified pending run."""
    import sys as _sys, os as _os
    _chase = _os.path.dirname(_os.path.abspath(__file__))
    if _chase not in _sys.path:
        _sys.path.insert(0, _chase)
    from db import get_unnotified_pending_runs, mark_owner_notified

    runs = get_unnotified_pending_runs()
    for run in runs:
        run_id   = run["id"]
        doc_name = run["doc_name"]

        by_owner: dict[str, list] = {}
        for f in run.get("findings", []):
            owner = f.get("owner_username") or "nacho"
            by_owner.setdefault(owner, []).append({
                "title":      f.get("rule_code") or f.get("detail", "Issue"),
                "location":   f.get("location", ""),
                "suggestion": f.get("proposed_fix", ""),
                "severity":   f.get("severity", "medium"),
            })

        for owner, owner_findings in by_owner.items():
            if send_finding(owner, doc_name, owner_findings, run_id=run_id):
                mark_owner_notified(run_id, owner)
                print(f"[notifier] ✉️  {owner} ← {doc_name!r} (run #{run_id})")


def start_notifier_daemon(interval: int = 30) -> None:
    """Start a background daemon that polls the DB every `interval` seconds."""
    def _loop():
        print(f"[notifier] Daemon started (poll every {interval}s)")
        while True:
            try:
                _poll_once()
            except Exception as exc:
                print(f"[notifier] Poll error: {exc}")
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="notifier-daemon")
    t.start()


def send_text(owner: str, text: str) -> bool:
    """Send a plain text message to an owner (for status updates)."""
    chat_id = OWNER_MAP.get(owner)
    if not chat_id:
        return False
    r = requests.post(
        f"{TG_API}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    return r.ok

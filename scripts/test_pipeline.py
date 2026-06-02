#!/usr/bin/env python3
"""
test_pipeline.py — tests the partial pipeline:
  connector.py  →  mini_critic (regex PII)  →  db.py  →  notifier.py  →  Telegram

Run:  python3 scripts/test_pipeline.py [doc_path]
"""

import os
import re
import sys
import json
from pathlib import Path
pass  # env loaded manually below

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "chase"))
sys.path.insert(0, str(_REPO_ROOT / "agents"))

# ---------------------------------------------------------------------------
# Load env — .env first, then fall back to luca's credentials
# ---------------------------------------------------------------------------

env_path = _REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Override DB to luca (we know tables exist there)
creds_path = Path(os.getenv("PMAAS_CREDENTIALS", str(Path.home() / ".pmaas/credentials")))
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
# Mini-critic — regex PII detector (substitute for the real Critic Agent)
# ---------------------------------------------------------------------------

DNI_RE    = re.compile(r'\b\d{8}[A-Z]\b')
PHONE_RE  = re.compile(r'(\+34\s?\d{3}\s?\d{3}\s?\d{3}|\+1\s?\d{3}\s?\d{3}\s?\d{4}|\b6\d{2}\s?\d{3}\s?\d{3}\b)')
EMAIL_RE  = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+-internal\.com\b|'
                       r'\b[a-zA-Z0-9._%+\-]+@gmail\.com\b')
TBD_RE    = re.compile(r'\b(TBD|TODO|pendiente|PENDIENTE|a completar|\[TBD\]|\[INSERT[^\]]+\]|\[PENDING[^\]]+\])\b')
NAMING_RE = re.compile(r'(FINAL|final|_v\d+|_V\d+)')


def mini_critic(doc: dict) -> list[dict]:
    """Detect PII and compliance issues from connector output."""
    text = doc["text"] + "\n" + (doc.get("notes") or "")
    findings = []

    for m in DNI_RE.finditer(text):
        findings.append({
            "title":      f"PII expuesta: DNI encontrado ({m.group()})",
            "location":   _locate(text, m.start(), doc),
            "suggestion": 'Reemplazar con "[DNI ANONIMIZADO]" según estándar GDPR',
            "severity":   "high",
        })

    for m in EMAIL_RE.finditer(text):
        findings.append({
            "title":      f"PII expuesta: email corporativo ({m.group()})",
            "location":   _locate(text, m.start(), doc),
            "suggestion": "Eliminar o anonimizar direcciones de email en documentos compartibles",
            "severity":   "high",
        })

    for m in PHONE_RE.finditer(text):
        findings.append({
            "title":      f"PII expuesta: número de teléfono ({m.group().strip()})",
            "location":   _locate(text, m.start(), doc),
            "suggestion": "Eliminar número de teléfono del documento",
            "severity":   "medium",
        })

    tbds = set(m.group() for m in TBD_RE.finditer(text))
    if tbds:
        findings.append({
            "title":      f"Valores placeholder detectados: {', '.join(sorted(tbds)[:4])}",
            "location":   "Múltiples secciones del documento",
            "suggestion": "Completar todos los campos TBD/TODO/pendiente antes de distribuir",
            "severity":   "medium",
        })

    fname = doc["filename"]
    if NAMING_RE.search(fname) or (not re.match(r'^\d{4}-\d{2}-\d{2}_', fname) and doc["format"] in ("pdf",)):
        findings.append({
            "title":      f"Naming convention incorrecto: '{fname}'",
            "location":   "Nombre del fichero",
            "suggestion": "Renombrar siguiendo el estándar YYYY-MM-DD_tipo_nombre",
            "severity":   "low",
        })

    return findings


def _locate(text: str, pos: int, doc: dict) -> str:
    """Guess section name from character position."""
    snippet = text[max(0, pos-80):pos].rsplit("\n", 1)[-1]
    for s in doc.get("sections", []):
        if s["heading"] and s["heading"] in text[max(0,pos-300):pos+300]:
            return f'Sección "{s["heading"]}"'
    if doc.get("notes") and pos >= len(doc["text"]):
        return "Speaker notes (no visibles en el documento)"
    return "Cuerpo del documento"


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run(doc_path: str):
    from agents.connector import load_document
    from chase.db       import create_run
    from chase.notifier import send_finding

    print(f"\n{'='*55}")
    print(f"  DAVE Pipeline Test")
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

    # Step 3 — DB
    print(f"\n[3/4] DB: creating validation run ...")
    try:
        run_id = create_run(doc["filename"], "luca", findings)
        print(f"  run_id = {run_id}  ({len(findings)} findings saved)")
    except Exception as e:
        print(f"  DB error: {e}")
        run_id = None

    # Step 4 — Telegram
    print(f"\n[4/4] Telegram: sending notification to luca ...")
    ok = send_finding("luca", doc["filename"], findings, run_id=run_id)
    if ok:
        print(f"  Mensaje enviado ✓  (run #{run_id})")
        print(f"\n  Revisa tu Telegram — deberías recibir el mensaje con los botones.")
    else:
        print(f"  ERROR al enviar. Revisa TELEGRAM_BOT_TOKEN y TELEGRAM_LUCA_CHAT_ID.")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    doc = sys.argv[1] if len(sys.argv) > 1 else str(_REPO_ROOT / "test_docs/contrato_juan_garcia_2024.docx")
    run(doc)

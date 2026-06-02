#!/usr/bin/env python3
"""
export_results.py — exports validation_runs + findings from DB to data/export_results.json
(override with EXPORT_OUT_PATH env var)
Run continuously: while true; do python3 export_results.py; sleep 60; done
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Load credentials
# --------------------------------------------------------------------------
creds = Path(os.getenv("PMAAS_CREDENTIALS", str(Path.home() / ".pmaas/credentials")))
for line in creds.read_text().splitlines():
    line = line.strip().lstrip("export ").strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import psycopg2
import psycopg2.extras

# --------------------------------------------------------------------------
# Connect
# --------------------------------------------------------------------------
conn = psycopg2.connect(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ.get("DB_PASSWORD", ""),
)
conn.autocommit = True
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# --------------------------------------------------------------------------
# Query runs with findings
# --------------------------------------------------------------------------
cur.execute("""
    SELECT v.id, v.document, v.owner, v.status, v.created_at, v.updated_at,
           COUNT(f.id) FILTER (WHERE f.status = 'open')     AS open_count,
           COUNT(f.id) FILTER (WHERE f.status = 'resolved') AS resolved_count,
           COUNT(f.id) AS total_findings
    FROM validation_runs v
    LEFT JOIN findings f ON f.run_id = v.id
    GROUP BY v.id
    ORDER BY v.created_at DESC
    LIMIT 50
""")
runs_raw = cur.fetchall()

# --------------------------------------------------------------------------
# Query findings per run
# --------------------------------------------------------------------------
cur.execute("""
    SELECT id, run_id, title, location, suggestion, severity, status, created_at
    FROM findings
    ORDER BY run_id DESC, id ASC
""")
findings_raw = cur.fetchall()

cur.close()
conn.close()

# --------------------------------------------------------------------------
# Build output structure
# --------------------------------------------------------------------------
findings_by_run = {}
for f in findings_raw:
    rid = f["run_id"]
    if rid not in findings_by_run:
        findings_by_run[rid] = []
    findings_by_run[rid].append({
        "id":         f["id"],
        "title":      f["title"],
        "location":   f["location"],
        "suggestion": f["suggestion"],
        "severity":   f["severity"],
        "status":     f["status"],
        "created_at": str(f["created_at"]),
    })

runs_out = []
total_open = total_resolved = 0
sev_counts = {"high": 0, "medium": 0, "low": 0}
docs_set = set()

for r in runs_raw:
    rid = r["id"]
    fs  = findings_by_run.get(rid, [])
    total_open     += r["open_count"]
    total_resolved += r["resolved_count"]
    docs_set.add(r["document"])
    for f in fs:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1

    runs_out.append({
        "id":              rid,
        "document":        r["document"],
        "owner":           r["owner"],
        "status":          r["status"],
        "created_at":      str(r["created_at"]),
        "updated_at":      str(r["updated_at"]),
        "total_findings":  r["total_findings"],
        "open_count":      r["open_count"],
        "resolved_count":  r["resolved_count"],
        "findings":        fs,
    })

total_findings = total_open + total_resolved
fix_rate = round((total_resolved / total_findings * 100) if total_findings else 0, 1)

output = {
    "exported_at": datetime.now(timezone.utc).isoformat(),
    "stats": {
        "total_runs":      len(runs_out),
        "total_findings":  total_findings,
        "open":            total_open,
        "resolved":        total_resolved,
        "fix_rate":        fix_rate,
        "by_severity":     sev_counts,
        "docs_analyzed":   len(docs_set),
    },
    "runs": runs_out,
}

# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
out_path = Path(os.getenv("EXPORT_OUT_PATH", str(Path(__file__).resolve().parent.parent / "data/export_results.json")))
out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
print(f"[{datetime.now().strftime('%H:%M:%S')}] Exported {len(runs_out)} runs, {total_findings} findings → {out_path}")

"""
Database layer for DAVE — matches the real schema created by Nacho.

Tables:
  validation_runs(id, doc_name, doc_type, doc_path, started_at, finished_at,
                  findings_count, status, error_message)
  findings(id, run_id, doc_name, doc_url, owner_username, rule_code, severity,
           detail, proposed_fix, location, status, notified_at, resolved_at,
           resolution, created_at, updated_at)
  owner_map(id, department, platform_username, telegram_chat_id)
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor


def _conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "dave"),
        user=os.getenv("DB_USER", "dave"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def create_run(document: str, owner: str, findings: list[dict],
               doc_type: str = "unknown", doc_path: str = "") -> int:
    """
    Insert a validation run and its findings. Returns run_id.

    findings dicts accept both old keys (title, suggestion) and new schema keys
    (rule_code, detail, proposed_fix) for compatibility with notifier.py.
    owner: platform_username (e.g. 'augusto')
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO validation_runs (doc_name, doc_type, doc_path, status, findings_count)
               VALUES (%s, %s, %s, 'pending', %s) RETURNING id""",
            (document, doc_type, doc_path, len(findings)),
        )
        run_id = cur.fetchone()[0]

        for f in findings:
            rule_code    = f.get("rule_code") or f.get("title", "")
            detail       = f.get("detail")    or f.get("title", "")
            proposed_fix = f.get("proposed_fix") or f.get("suggestion", "")
            cur.execute(
                """INSERT INTO findings
                   (run_id, doc_name, owner_username, rule_code, severity,
                    detail, proposed_fix, location, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
                (run_id, document, owner, rule_code,
                 f.get("severity", "medium"), detail, proposed_fix,
                 f.get("location", "")),
            )
        conn.commit()
    return run_id


# validation_runs accepts all 6 statuses; findings only allows these 3.
# pending_fix / partial_fix are run-level signals — findings stay pending
# until update_finding_status() resolves them individually.
_FINDINGS_STATUS_MAP = {
    "fixed":   "fixed",
    "manual":  "manual",
    "ignored": "ignored",
}


def update_run_status(run_id: int, status: str) -> None:
    """Update status on validation_run and, where applicable, on its findings."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE validation_runs SET status=%s, finished_at=NOW() WHERE id=%s",
            (status, run_id),
        )
        findings_status = _FINDINGS_STATUS_MAP.get(status)
        if findings_status:
            cur.execute(
                "UPDATE findings SET status=%s WHERE run_id=%s",
                (findings_status, run_id),
            )
        conn.commit()


def update_finding_status(finding_id: int, status: str, resolution: str = "") -> None:
    """Update a single finding's status (fixed, manual, ignored)."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE findings SET status=%s, resolution=%s, resolved_at=NOW()
               WHERE id=%s""",
            (status, resolution, finding_id),
        )
        conn.commit()


def mark_notified(run_id: int) -> None:
    """Set notified_at on all pending findings of a run."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE findings SET notified_at=NOW() WHERE run_id=%s AND status='pending'",
            (run_id,),
        )
        conn.commit()


def get_run(run_id: int) -> dict | None:
    """Return a run with its findings, or None."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM validation_runs WHERE id=%s", (run_id,))
        run = cur.fetchone()
        if not run:
            return None
        cur.execute("SELECT * FROM findings WHERE run_id=%s ORDER BY id", (run_id,))
        run = dict(run)
        run["findings"] = [dict(r) for r in cur.fetchall()]
    return run


def get_pending_runs() -> list[dict]:
    """Return all runs with status 'pending'."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM validation_runs WHERE status='pending' ORDER BY started_at",
        )
        return [dict(r) for r in cur.fetchall()]


def get_telegram_chat_id(owner_username: str) -> int | None:
    """Look up telegram_chat_id from owner_map by platform_username."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT telegram_chat_id FROM owner_map WHERE platform_username=%s LIMIT 1",
            (owner_username,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def upsert_owner(department: str, username: str, telegram_chat_id: int) -> None:
    """Insert or update an owner in owner_map."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO owner_map (department, platform_username, telegram_chat_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (department, platform_username)
               DO UPDATE SET telegram_chat_id=EXCLUDED.telegram_chat_id""",
            (department, username, telegram_chat_id),
        )
        conn.commit()


# ── Analytics queries (read-only) ────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    """KPI summary for the Analytics tab."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS total_runs FROM validation_runs")
        runs_row = dict(cur.fetchone())

        cur.execute("""
            SELECT
                COUNT(*)                                                    AS total,
                COUNT(*) FILTER (WHERE status = 'pending')                  AS open,
                COUNT(*) FILTER (WHERE status = 'fixed')                    AS fixed,
                COUNT(*) FILTER (WHERE status = 'manual')                   AS manual,
                COUNT(*) FILTER (WHERE status = 'ignored')                  AS ignored
            FROM findings
        """)
        f = dict(cur.fetchone())

    total    = f["total"]  or 0
    resolved = (f["fixed"] or 0) + (f["manual"] or 0)
    fix_rate = round(resolved / total * 100, 1) if total else 0.0
    return {
        "total_runs":     runs_row["total_runs"],
        "total_findings": total,
        "open":           f["open"]    or 0,
        "fixed":          f["fixed"]   or 0,
        "manual":         f["manual"]  or 0,
        "ignored":        f["ignored"] or 0,
        "fix_rate":       fix_rate,
    }


def get_findings_by_severity() -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT severity, COUNT(*) AS count
            FROM findings
            GROUP BY severity
            ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
        """)
        return [dict(r) for r in cur.fetchall()]


def get_findings_by_status() -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT status, COUNT(*) AS count
            FROM findings
            GROUP BY status
            ORDER BY count DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_top_rule_codes(limit: int = 10) -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT rule_code, COUNT(*) AS count
            FROM findings
            WHERE rule_code IS NOT NULL AND rule_code != ''
            GROUP BY rule_code
            ORDER BY count DESC
            LIMIT %s
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_findings_by_owner() -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                owner_username,
                COUNT(*) FILTER (WHERE status = 'pending')                        AS open,
                COUNT(*) FILTER (WHERE status IN ('fixed', 'manual', 'ignored'))  AS resolved
            FROM findings
            WHERE owner_username IS NOT NULL AND owner_username != ''
            GROUP BY owner_username
            ORDER BY open DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_runs_over_time() -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT DATE(started_at) AS day, COUNT(*) AS runs
            FROM validation_runs
            WHERE started_at IS NOT NULL
            GROUP BY day
            ORDER BY day
        """)
        return [{"day": str(r["day"]), "runs": r["runs"]} for r in cur.fetchall()]

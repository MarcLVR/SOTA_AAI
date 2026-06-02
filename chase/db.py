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


def update_run_status(run_id: int, status: str) -> None:
    """Update status on validation_run and all its findings."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE validation_runs SET status=%s, finished_at=NOW() WHERE id=%s",
            (status, run_id),
        )
        cur.execute(
            "UPDATE findings SET status=%s WHERE run_id=%s",
            (status, run_id),
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

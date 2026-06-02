-- DAVE database schema
-- Matches exactly what chase/db.py reads and writes.
-- Apply to a fresh PostgreSQL database:
--   psql -U dave -d dave -f chase/schema.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- validation_runs
--
-- One row per audit run initiated by notifier.send_finding() or create_run().
-- Columns sourced from db.py queries:
--   doc_name, doc_type, doc_path, status, findings_count   ← INSERT (create_run)
--   finished_at                                             ← UPDATE (update_run_status)
--   started_at                                              ← ORDER BY (get_pending_runs)
--   error_message                                           ← docstring only; no query writes it
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS validation_runs (
    id              SERIAL          PRIMARY KEY,
    doc_name        TEXT            NOT NULL,
    doc_type        TEXT            NOT NULL    DEFAULT 'unknown',
    doc_path        TEXT            NOT NULL    DEFAULT '',
    status          TEXT            NOT NULL    DEFAULT 'pending'
                        CHECK (status IN (
                            'pending', 'pending_fix', 'manual',
                            'ignored', 'fixed', 'partial_fix'
                        )),
    findings_count  INTEGER         NOT NULL    DEFAULT 0,
    started_at      TIMESTAMPTZ     NOT NULL    DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    error_message   TEXT                                        -- never written by db.py; reserved
);

-- Indexes used by db.py filter/sort patterns
CREATE INDEX IF NOT EXISTS idx_validation_runs_status
    ON validation_runs (status);

CREATE INDEX IF NOT EXISTS idx_validation_runs_started_at
    ON validation_runs (started_at);


-- ─────────────────────────────────────────────────────────────────────────────
-- findings
--
-- One row per individual finding within a run.
-- Columns sourced from db.py queries:
--   run_id, doc_name, owner_username, rule_code, severity,
--   detail, proposed_fix, location, status                  ← INSERT (create_run)
--   notified_at                                             ← UPDATE (mark_notified)
--   resolved_at, resolution                                 ← UPDATE (update_finding_status)
--   doc_url                                                 ← docstring only; never inserted
--   created_at                                              ← DEFAULT NOW(); never set explicitly
--   updated_at                                              ← DEFAULT NOW(); see NOTE below
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS findings (
    id              SERIAL          PRIMARY KEY,
    run_id          INTEGER         NOT NULL
                        REFERENCES validation_runs (id) ON DELETE CASCADE,
    doc_name        TEXT            NOT NULL,
    doc_url         TEXT,                                       -- never populated by db.py; nullable
    owner_username  TEXT            NOT NULL,
    rule_code       TEXT            NOT NULL,
    severity        TEXT            NOT NULL    DEFAULT 'medium'
                        CHECK (severity IN ('high', 'medium', 'low')),
    detail          TEXT            NOT NULL    DEFAULT '',
    proposed_fix    TEXT            NOT NULL    DEFAULT '',
    location        TEXT            NOT NULL    DEFAULT '',
    status          TEXT            NOT NULL    DEFAULT 'pending'
                        CHECK (status IN ('pending', 'fixed', 'manual', 'ignored')),
    notified_at     TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT,
    created_at      TIMESTAMPTZ     NOT NULL    DEFAULT NOW(),
    -- NOTE: db.py's update_finding_status does NOT set updated_at=NOW().
    -- The trigger below keeps it accurate without requiring a db.py change.
    updated_at      TIMESTAMPTZ     NOT NULL    DEFAULT NOW()
);

-- Trigger: auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_findings_updated_at
    BEFORE UPDATE ON findings
    FOR EACH ROW EXECUTE FUNCTION _set_updated_at();

-- Indexes used by db.py filter/join patterns
CREATE INDEX IF NOT EXISTS idx_findings_run_id
    ON findings (run_id);

CREATE INDEX IF NOT EXISTS idx_findings_status
    ON findings (status);

CREATE INDEX IF NOT EXISTS idx_findings_owner_username
    ON findings (owner_username);


-- ─────────────────────────────────────────────────────────────────────────────
-- owner_map
--
-- Maps a (department, platform_username) pair to a Telegram chat ID.
-- Columns sourced from db.py queries:
--   department, platform_username, telegram_chat_id         ← INSERT/UPSERT (upsert_owner)
--   telegram_chat_id                                        ← SELECT (get_telegram_chat_id)
--   platform_username                                       ← WHERE (get_telegram_chat_id)
-- The ON CONFLICT in upsert_owner requires a UNIQUE constraint on
-- (department, platform_username).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS owner_map (
    id                  SERIAL      PRIMARY KEY,
    department          TEXT        NOT NULL,
    platform_username   TEXT        NOT NULL,
    telegram_chat_id    BIGINT      NOT NULL,   -- Telegram IDs exceed INTEGER range
    UNIQUE (department, platform_username)
);

-- Index used by get_telegram_chat_id WHERE platform_username=
CREATE INDEX IF NOT EXISTS idx_owner_map_platform_username
    ON owner_map (platform_username);

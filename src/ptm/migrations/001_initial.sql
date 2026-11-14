-- PTM V0.3 initial schema

CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    parent_id TEXT REFERENCES category(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_category_parent_name
    ON category(COALESCE(parent_id, ''), name)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS matter (
    id TEXT PRIMARY KEY,
    category_id TEXT REFERENCES category(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL CHECK (status IN ('backlog', 'active', 'paused', 'completed', 'cancelled')),
    priority TEXT CHECK (priority IS NULL OR priority IN ('low', 'normal', 'high', 'urgent')),
    due_at TEXT,
    estimated_effort_minutes INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_matter_status ON matter(status);
CREATE INDEX IF NOT EXISTS ix_matter_category ON matter(category_id);
CREATE INDEX IF NOT EXISTS ix_matter_due_at ON matter(due_at);

CREATE TABLE IF NOT EXISTS stage (
    id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL REFERENCES matter(id),
    parent_stage_id TEXT REFERENCES stage(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL CHECK (status IN ('backlog', 'active', 'paused', 'completed', 'cancelled')),
    sequence INTEGER,
    estimated_effort_minutes INTEGER,
    completion_criteria TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_stage_matter ON stage(matter_id);
CREATE INDEX IF NOT EXISTS ix_stage_parent ON stage(parent_stage_id);

CREATE TABLE IF NOT EXISTS plan (
    id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL REFERENCES matter(id),
    stage_id TEXT REFERENCES stage(id),
    plan_type TEXT NOT NULL CHECK (plan_type IN ('time_block', 'time_budget')),
    start_at TEXT,
    end_at TEXT,
    estimated_minutes INTEGER,
    expected_result TEXT,
    status TEXT NOT NULL CHECK (status IN ('planned', 'completed', 'cancelled', 'missed')),
    flexibility TEXT NOT NULL CHECK (flexibility IN ('fixed', 'flexible')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_plan_matter ON plan(matter_id);
CREATE INDEX IF NOT EXISTS ix_plan_start ON plan(start_at);
CREATE INDEX IF NOT EXISTS ix_plan_status ON plan(status);

CREATE TABLE IF NOT EXISTS execution (
    id TEXT PRIMARY KEY,
    matter_id TEXT REFERENCES matter(id),
    stage_id TEXT REFERENCES stage(id),
    plan_id TEXT REFERENCES plan(id),
    start_at TEXT,
    end_at TEXT,
    duration_minutes INTEGER,
    duration_precision TEXT NOT NULL CHECK (duration_precision IN ('exact', 'approximate', 'unknown')),
    result TEXT NOT NULL CHECK (result IN ('completed', 'partial', 'interrupted', 'unknown')),
    output TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_execution_matter ON execution(matter_id);
CREATE INDEX IF NOT EXISTS ix_execution_plan ON execution(plan_id);
CREATE INDEX IF NOT EXISTS ix_execution_created ON execution(created_at);

CREATE TABLE IF NOT EXISTS observation (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('feedback', 'preference', 'pattern')),
    content TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('user', 'ai', 'statistics')),
    matter_id TEXT REFERENCES matter(id),
    execution_id TEXT REFERENCES execution(id),
    confidence REAL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'invalidated')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_observation_matter ON observation(matter_id);
CREATE INDEX IF NOT EXISTS ix_observation_status ON observation(status);

CREATE TABLE IF NOT EXISTS observation_evidence (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES observation(id),
    evidence_type TEXT NOT NULL,
    evidence_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_obs_evidence_obs ON observation_evidence(observation_id);

CREATE TABLE IF NOT EXISTS event (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_text TEXT,
    before_json TEXT,
    after_json TEXT,
    request_id TEXT
);

CREATE INDEX IF NOT EXISTS ix_event_entity ON event(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_event_occurred ON event(occurred_at);
CREATE INDEX IF NOT EXISTS ix_event_request ON event(request_id);

CREATE TABLE IF NOT EXISTS reminder (
    id TEXT PRIMARY KEY,
    matter_id TEXT REFERENCES matter(id),
    stage_id TEXT REFERENCES stage(id),
    plan_id TEXT REFERENCES plan(id),
    title TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'submitted', 'delivered', 'failed', 'cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_until TEXT,
    last_attempt_at TEXT,
    submitted_at TEXT,
    delivered_at TEXT,
    last_error TEXT,
    claim_token TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_reminder_status_at ON reminder(status, remind_at);
CREATE INDEX IF NOT EXISTS ix_reminder_claim ON reminder(claim_token);

CREATE TABLE IF NOT EXISTS operation_request (
    request_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preview_token (
    token TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

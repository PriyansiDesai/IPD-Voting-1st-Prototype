-- Migration 002: Operations, Audit, and Retention Fields

-- 1. Add fields for retention policy, early closure, and legal holds
ALTER TABLE voting_sessions ADD COLUMN IF NOT EXISTS certified_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE voting_sessions ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE voting_sessions ADD COLUMN IF NOT EXISTS early_close_reason TEXT;
ALTER TABLE voting_sessions ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Security Audit Log (Isolated from Voter Identity)
-- Do NOT store voter identity, reservation_token, idempotency_key, or ballot payload here.
CREATE TABLE IF NOT EXISTS security_audit_log (
    log_id SERIAL PRIMARY KEY,
    action_type TEXT NOT NULL,
    session_id TEXT,
    event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details TEXT NOT NULL
);

-- 3. Update session statuses to include DRAFT and APPROVED
-- (Status is checked at runtime; no schema constraint change needed if it was just TEXT)

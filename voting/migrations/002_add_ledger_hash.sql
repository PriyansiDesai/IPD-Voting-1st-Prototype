ALTER TABLE ballots ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE ballots DROP CONSTRAINT IF EXISTS ballots_session_id_fkey;
ALTER TABLE ballots ADD CONSTRAINT ballots_session_id_fkey FOREIGN KEY (session_id) REFERENCES voting_sessions(session_id);

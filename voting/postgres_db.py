import json
import hashlib
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv
import uuid

try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    from psycopg2 import sql
except ImportError:
    psycopg2 = None

class PostgresVotingRepository:
    def __init__(self, dsn: str):
        self.dsn = dsn
        if not psycopg2:
            raise ImportError("psycopg2 is required for PostgreSQL integration.")
        self.pool = psycopg2.pool.SimpleConnectionPool(1, 10, self.dsn)

    def get_connection(self):
        return self.pool.getconn()

    def run_migrations(self, custom_migrations_dir: Optional[Path] = None):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        hash TEXT NOT NULL,
                        applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='schema_migrations'")
                columns = [r[0] for r in cur.fetchall()]
                if 'name' not in columns:
                    cur.execute("ALTER TABLE schema_migrations ADD COLUMN name TEXT NOT NULL DEFAULT 'unknown'")
                if 'hash' not in columns:
                    cur.execute("ALTER TABLE schema_migrations ADD COLUMN hash TEXT NOT NULL DEFAULT 'unknown'")
                if 'applied_at' not in columns:
                    cur.execute("ALTER TABLE schema_migrations ADD COLUMN applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP")
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT version, hash FROM schema_migrations ORDER BY version ASC")
                applied = {row[0]: row[1] for row in cur.fetchall()}

            migrations_dir = custom_migrations_dir if custom_migrations_dir else Path(__file__).parent / 'migrations'
            if not migrations_dir.exists():
                raise FileNotFoundError(f"Migrations directory not found: {migrations_dir}")

            files = list(migrations_dir.glob('*.sql'))

            parsed_files = {}
            for f in files:
                try:
                    version = int(f.name.split('_')[0])
                except ValueError:
                    continue
                if version in parsed_files:
                    raise ValueError(f"Duplicate migration version found: {version}")
                parsed_files[version] = f

            sorted_versions = sorted(parsed_files.keys())

            for version in sorted_versions:
                f = parsed_files[version]
                with open(f, 'r', encoding='utf-8') as file:
                    content = file.read()

                file_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

                if version in applied:
                    if applied[version] == 'unknown':
                        # Update legacy hash
                        try:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE schema_migrations SET hash = %s WHERE version = %s", (file_hash, version))
                            conn.commit()
                        except Exception as e:
                            conn.rollback()
                            raise RuntimeError(f"Failed to update legacy hash for migration {f.name}: {e}")
                    elif applied[version] != file_hash:
                        raise ValueError(f"Migration {f.name} has changed since it was applied.")
                    continue

                try:
                    with conn.cursor() as cur:
                        cur.execute(content)
                        cur.execute(
                            "INSERT INTO schema_migrations (version, name, hash) VALUES (%s, %s, %s)",
                            (version, f.name, file_hash)
                        )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    raise RuntimeError(f"Migration {f.name} failed: {e}")
        finally:
            self.pool.putconn(conn)

    def import_csv_fixtures(self, data_dir: Path):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                def load_table(filename, table, columns):
                    path = data_dir / filename
                    if not path.exists():
                        raise ValueError(f"Missing required fixture: {filename}")
                    with open(path, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        data = []
                        for row in reader:
                            for c in columns:
                                if not row.get(c) or not row[c].strip():
                                    raise ValueError(f"Blank value for {c} in {filename}")
                            data.append(tuple(row[col] for col in columns))
                        if data:
                            placeholders = ','.join(['%s'] * len(columns))
                            cur.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", data)

                load_table('voters.csv', 'voters', ['voter_id', 'name', 'department', 'role'])
                load_table('candidates.csv', 'candidates', ['candidate_id', 'candidate_name'])
                load_table('ballot_options.csv', 'ballot_options', ['option_id', 'option_label', 'option_type'])

                # Sessions
                session_path = data_dir / 'voting_sessions.csv'
                if not session_path.exists():
                    raise ValueError("Missing voting_sessions.csv")
                with open(session_path, 'r', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        cur.execute(
                            "INSERT INTO voting_sessions (session_id, title, question, session_type, start_time, end_time, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (row['session_id'], row['title'], row.get('question'), row['session_type'], row['start_time'], row['end_time'], row['status'])
                        )

                load_table('session_voters.csv', 'session_voters', ['session_id', 'voter_id'])

                cur.execute("SELECT session_id, session_type FROM voting_sessions")
                stypes = {r[0]: r[1] for r in cur.fetchall()}

                sc_path = data_dir / 'session_candidates.csv'
                if sc_path.exists():
                    with open(sc_path, 'r', encoding='utf-8') as f:
                        for row in csv.DictReader(f):
                            if stypes.get(row['session_id']) != 'candidate_election':
                                raise ValueError("Invalid candidate mapping for non-candidate session")
                            cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id) VALUES (%s, %s, 'candidate_election', %s)",
                            (str(uuid.uuid4()), row['session_id'], row['candidate_id']))

                so_path = data_dir / 'session_options.csv'
                if so_path.exists():
                    with open(so_path, 'r', encoding='utf-8') as f:
                        for row in csv.DictReader(f):
                            stype = stypes.get(row['session_id'], 'unknown')
                            cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, option_id) VALUES (%s, %s, %s, %s)",
                            (str(uuid.uuid4()), row['session_id'], stype, row['option_id']))
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise ValueError(f"CSV Import Failed: {e}")
        finally:
            self.pool.putconn(conn)

    def reserve_vote(self, session_id: str, voter_id: str, idempotency_key: str) -> Tuple[bool, Optional[str], Optional[str]]:
        for attempt in range(5):
            conn = self.get_connection()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT status, start_time, end_time FROM voting_sessions WHERE session_id = %s FOR SHARE", (session_id,))
                    session = cur.fetchone()
                    if not session:
                        from voting.voting_engine import UnknownSessionError
                        raise UnknownSessionError(f"Session {session_id} not found.")

                    if session['status'] != 'ACTIVE':
                        from voting.voting_engine import InactiveSessionError
                        raise InactiveSessionError(f"Session {session_id} is not ACTIVE.")

                    cur.execute("SELECT CURRENT_TIMESTAMP")
                    now = cur.fetchone()['current_timestamp']
                    if now < session['start_time'] or now >= session['end_time']:
                        from voting.voting_engine import InactiveSessionError
                        raise InactiveSessionError("Current time is outside the valid session window.")

                    cur.execute("SELECT 1 FROM voters WHERE voter_id = %s", (voter_id,))
                    if not cur.fetchone():
                        from voting.voting_engine import UnknownVoterError
                        raise UnknownVoterError(f"Voter {voter_id} not found.")

                    cur.execute("SELECT 1 FROM session_voters WHERE session_id = %s AND voter_id = %s", (session_id, voter_id))
                    if not cur.fetchone():
                        from voting.voting_engine import IneligibleVoterError
                        raise IneligibleVoterError(f"Voter {voter_id} is not eligible for session {session_id}.")

                    cur.execute("SELECT status, idempotency_key, receipt_id, reservation_token, reserved_at FROM voter_participation WHERE session_id = %s AND voter_id = %s", (session_id, voter_id))
                    part = cur.fetchone()

                    token = str(uuid.uuid4())
                    should_continue = False
                    if part:
                        if part['status'] == 'COMMITTED':
                            if part['idempotency_key'] == idempotency_key:
                                return False, None, part['receipt_id']
                            else:
                                from voting.voting_engine import DuplicateVoteError
                                raise DuplicateVoteError("Voter has already cast a vote.")
                        elif part['status'] == 'PENDING':
                            res_ts = part['reserved_at']
                            if now - res_ts <= timedelta(minutes=5):
                                if part['idempotency_key'] == idempotency_key:
                                    return True, part['reservation_token'], None
                                else:
                                    from voting.voting_engine import DuplicateVoteError
                                    raise DuplicateVoteError("A vote is currently being processed.")
                            else:
                                # Reclaim stale reservation atomically
                                cur.execute("""
                                    UPDATE voter_participation
                                    SET reservation_token = %s, reserved_at = CURRENT_TIMESTAMP, idempotency_key = %s
                                    WHERE session_id = %s AND voter_id = %s AND reservation_token = %s AND status = 'PENDING'
                                """, (token, idempotency_key, session_id, voter_id, part['reservation_token']))

                                if cur.rowcount == 0:
                                    conn.rollback()
                                    should_continue = True
                                else:
                                    conn.commit()
                                    return True, token, None

                    if not should_continue:
                        cur.execute("""
                            INSERT INTO voter_participation (session_id, voter_id, reservation_token, status, idempotency_key, reserved_at)
                            VALUES (%s, %s, %s, 'PENDING', %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (session_id, voter_id) DO NOTHING
                        """, (session_id, voter_id, token, idempotency_key))

                        if cur.rowcount == 0:
                            conn.rollback()
                            should_continue = True
                        else:
                            conn.commit()
                            return True, token, None

            except Exception:
                conn.rollback()
                raise
            finally:
                self.pool.putconn(conn)

            if should_continue:
                continue

        raise RuntimeError("Failed to reserve vote after maximum retries due to concurrency.")

    def validate_choice(self, session_id: str, choice_id: str) -> bool:
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM voting_sessions WHERE session_id = %s", (session_id,))
                if not cur.fetchone():
                    from voting.voting_engine import UnknownSessionError
                    raise UnknownSessionError(f"Session {session_id} not found.")

                cur.execute("""
                    SELECT 1 FROM session_choices
                    WHERE session_id = %s AND (candidate_id = %s OR option_id = %s)
                """, (session_id, choice_id, choice_id))
                return cur.fetchone() is not None
        finally:
            self.pool.putconn(conn)

    def finalize_vote(self, session_id: str, voter_id: str, reservation_token: str, encrypted_payload: str) -> str:
        import uuid
        try:
            uuid.UUID(str(reservation_token))
        except ValueError:
            raise ValueError(f"Invalid reservation token format: {reservation_token}")

        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    UPDATE voter_participation
                    SET status = 'COMMITTED', reservation_token = NULL
                    WHERE session_id = %s AND voter_id = %s AND reservation_token = %s AND status = 'PENDING'
                      AND reserved_at >= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                """, (session_id, voter_id, reservation_token))

                if cur.rowcount == 0:
                    raise ValueError("Reservation is stale, expired, or invalid.")

                cur.execute("INSERT INTO ballots (session_id, encrypted_vote_payload) VALUES (%s, %s) RETURNING ballot_id", (session_id, encrypted_payload))
                ballot_id = cur.fetchone()['ballot_id']

                cur.execute("SELECT block_index FROM audit_ledger WHERE session_id = %s ORDER BY block_index DESC LIMIT 1", (session_id,))
                last_block = cur.fetchone()

                new_index = last_block['block_index'] + 1 if last_block else 0

                cur.execute("""
                    INSERT INTO audit_ledger (session_id, block_index, timestamp, encrypted_vote_payload)
                    VALUES (%s, %s, CURRENT_TIMESTAMP, %s)
                """, (session_id, new_index, encrypted_payload))

                receipt_id = str(uuid.uuid4())
                cur.execute("UPDATE voter_participation SET receipt_id = %s WHERE session_id = %s AND voter_id = %s", (receipt_id, session_id, voter_id))

                conn.commit()
                return receipt_id
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def get_session_details(self, session_id: str):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM voting_sessions WHERE session_id = %s', (session_id,))
                return cur.fetchone()
        finally:
            self.pool.putconn(conn)

    def get_voter_id_by_identity(self, issuer: str, subject: str) -> Optional[str]:
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT voter_id FROM voter_identities WHERE issuer = %s AND subject = %s", (issuer, subject))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            self.pool.putconn(conn)

    def get_session_choices(self, session_id: str):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT session_type FROM voting_sessions WHERE session_id = %s', (session_id,))
                sess = cur.fetchone()
                if not sess:
                    return None
                stype = sess['session_type']
                cur.execute('SELECT candidate_id, option_id FROM session_choices WHERE session_id = %s', (session_id,))
                choices = []
                for row in cur.fetchall():
                    if stype == 'candidate_election' and row['candidate_id']:
                        choices.append(row['candidate_id'])
                    elif stype in ('yes_no', 'single_choice') and row['option_id']:
                        choices.append(row['option_id'])
                return sorted(choices)
        finally:
            self.pool.putconn(conn)

    def has_voter_voted(self, session_id: str, voter_id: str) -> bool:
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT status FROM voter_participation WHERE session_id = %s AND voter_id = %s', (session_id, voter_id))
                row = cur.fetchone()
                if row and row['status'] == 'COMMITTED':
                    return True
                return False
        finally:
            self.pool.putconn(conn)

    def setup_session(self, session_id: str, title: str, session_type: str, start_time: datetime, end_time: datetime, voter_ids: List[str], choice_ids: List[str], question: Optional[str] = None) -> None:
        """Create a new voting session in UPCOMING status with voters and choices assigned atomically."""
        if start_time.tzinfo is None or start_time.utcoffset() is None or end_time.tzinfo is None or end_time.utcoffset() is None:
            raise ValueError("start_time and end_time must be timezone-aware.")
        if start_time >= end_time:
            raise ValueError("start_time must be strictly before end_time.")

        valid_types = {'candidate_election', 'yes_no', 'single_choice'}
        if session_type not in valid_types:
            raise ValueError(f"Invalid session_type. Must be one of {valid_types}")

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO voting_sessions (session_id, title, question, session_type, start_time, end_time, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'DRAFT')",
                    (session_id, title, question, session_type, start_time, end_time)
                )

                if voter_ids:
                    args = [(session_id, vid) for vid in voter_ids]
                    cur.executemany("INSERT INTO session_voters (session_id, voter_id) VALUES (%s, %s)", args)

                if choice_ids:
                    for cid in choice_ids:
                        choice_uuid = str(uuid.uuid4())
                        if session_type == 'candidate_election':
                            cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id) VALUES (%s, %s, %s, %s)", (choice_uuid, session_id, session_type, cid))
                        else:
                            cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, option_id) VALUES (%s, %s, %s, %s)", (choice_uuid, session_id, session_type, cid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def update_session_status(self, session_id: str, new_status: str, approver_id: Optional[str] = None, reason: Optional[str] = None) -> None:
        """Open, close, or certify a session by updating its status. Logs audit events."""
        valid_statuses = {'DRAFT', 'APPROVED', 'UPCOMING', 'ACTIVE', 'COMPLETED'}
        if new_status not in valid_statuses:
            raise ValueError(f"Invalid status. Must be one of {valid_statuses}")

        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT status, start_time, end_time FROM voting_sessions WHERE session_id = %s FOR UPDATE", (session_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError("Session not found.")

                current_status = row['status']

                # Explicit state machine
                valid_transitions = {
                    'DRAFT': {'APPROVED', 'COMPLETED'},
                    'APPROVED': {'ACTIVE', 'COMPLETED'},
                    'UPCOMING': {'ACTIVE', 'COMPLETED'}, # Keeping UPCOMING for backward compatibility
                    'ACTIVE': {'COMPLETED'},
                    'COMPLETED': set()
                }

                if new_status not in valid_transitions[current_status] and current_status != new_status:
                    raise ValueError(f"Invalid transition from {current_status} to {new_status}")

                if current_status in ('DRAFT', 'APPROVED', 'UPCOMING') and new_status == 'ACTIVE':
                    cur.execute("SELECT COUNT(*) as count FROM session_voters WHERE session_id = %s", (session_id,))
                    if cur.fetchone()['count'] == 0:
                        raise ValueError("Cannot open a session without eligible voters.")

                    cur.execute("SELECT COUNT(*) as count FROM session_choices WHERE session_id = %s", (session_id,))
                    if cur.fetchone()['count'] == 0:
                        raise ValueError("Cannot open a session without valid choices.")

                    cur.execute("SELECT CURRENT_TIMESTAMP")
                    now = cur.fetchone()['current_timestamp']
                    if now < row['start_time'] or now >= row['end_time']:
                        raise ValueError("Cannot open session outside of its configured time window.")

                if current_status != new_status:
                    if new_status == 'COMPLETED':
                        cur.execute("SELECT CURRENT_TIMESTAMP")
                        now = cur.fetchone()['current_timestamp']
                        if now < row['end_time']:
                            if not approver_id or not reason:
                                raise ValueError("Early closure requires a second approver_id and a recorded reason.")
                            cur.execute("UPDATE voting_sessions SET status = %s, closed_at = CURRENT_TIMESTAMP, early_close_reason = %s WHERE session_id = %s", (new_status, reason, session_id))
                        else:
                            cur.execute("UPDATE voting_sessions SET status = %s, closed_at = CURRENT_TIMESTAMP WHERE session_id = %s", (new_status, session_id))
                    else:
                        cur.execute("UPDATE voting_sessions SET status = %s WHERE session_id = %s", (new_status, session_id))

                    self._log_security_audit_internal(cur, "STATUS_CHANGE", session_id, f"Transitioned from {current_status} to {new_status}. Approver: {approver_id or 'Auto'}. Reason: {reason or 'N/A'}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def certify_session(self, session_id: str, approver_id: str) -> None:
        """Mark a session as certified, starting the retention countdown."""
        if not approver_id:
            raise ValueError("approver_id is required for certification.")

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP WHERE session_id = %s AND status = 'COMPLETED'", (session_id,))
                if cur.rowcount == 0:
                    raise ValueError(f"Session {session_id} not found or not in COMPLETED status.")
                self._log_security_audit_internal(cur, "CERTIFY_SESSION", session_id, f"Session certified by {approver_id}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def toggle_legal_hold(self, session_id: str, active: bool, approver_id: str) -> None:
        """Place or remove a legal hold on a session to prevent pruning."""
        if not approver_id:
            raise ValueError("approver_id is required to modify legal holds.")

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE voting_sessions SET legal_hold = %s WHERE session_id = %s", (active, session_id))
                if cur.rowcount == 0:
                    raise ValueError(f"Session {session_id} not found.")
                action = "PLACED" if active else "REMOVED"
                self._log_security_audit_internal(cur, f"LEGAL_HOLD_{action}", session_id, f"Legal hold {action.lower()} by {approver_id}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def _log_security_audit_internal(self, cur, action_type: str, session_id: Optional[str], details: str) -> None:
        """Internal helper to log audit events within an existing transaction. DOES NOT store voter identity."""
        cur.execute(
            "INSERT INTO security_audit_log (action_type, session_id, details) VALUES (%s, %s, %s)",
            (action_type, session_id, details)
        )

    def prune_retained_data(self) -> dict:
        """
        Enforce data retention policy:
        1. Voter links (PII, identity, participation) removed >30 days after result certification.
        2. Encrypted ballots and non-identifying evidence (audit_ledger) removed >1 year after result certification.
        3. Security audit logs removed >1 year, preserving logs for sessions on legal hold.
        Sessions under an active legal hold are NEVER pruned.
        Voter master records are ALWAYS preserved.
        NOTE: Returns counts of deleted rows.
        """
        import os
        retention_url = os.environ.get("RETENTION_DATABASE_URL")
        if not retention_url:
            raise ValueError("RETENTION_DATABASE_URL is not configured for pruning. Refusing to fall back to runtime credentials.")

        results = {"pii_deleted": 0, "election_data_deleted": 0, "audit_logs_deleted": 0}

        # Use a one-off connection for the retention role
        import psycopg2
        conn = psycopg2.connect(retention_url)
        try:
            with conn.cursor() as cur:
                # 1. Delete PII for sessions certified > 30 days ago and not on legal hold
                cur.execute("SELECT session_id FROM voting_sessions WHERE certified_at < CURRENT_TIMESTAMP - INTERVAL '30 days' AND legal_hold = FALSE FOR SHARE SKIP LOCKED")
                cert_sessions = [r[0] for r in cur.fetchall()]

                if cert_sessions:
                    for sid in cert_sessions:
                        cur.execute("DELETE FROM session_voters WHERE session_id = %s", (sid,))
                        results["pii_deleted"] += cur.rowcount
                        cur.execute("DELETE FROM voter_participation WHERE session_id = %s", (sid,))
                        results["pii_deleted"] += cur.rowcount

                # 2. Delete encrypted election data for sessions certified > 1 year ago and not on legal hold
                cur.execute("SELECT session_id FROM voting_sessions WHERE certified_at < CURRENT_TIMESTAMP - INTERVAL '1 year' AND legal_hold = FALSE FOR SHARE SKIP LOCKED")
                expired_sessions = [r[0] for r in cur.fetchall()]

                if expired_sessions:
                    for sid in expired_sessions:
                        cur.execute("DELETE FROM ballots WHERE session_id = %s", (sid,))
                        results["election_data_deleted"] += cur.rowcount
                        cur.execute("DELETE FROM audit_ledger WHERE session_id = %s", (sid,))
                        results["election_data_deleted"] += cur.rowcount

                # 3. Delete old audit logs, preserving sessions on legal hold
                # A subquery with FOR SHARE SKIP LOCKED ensures we do not delete logs for a session
                # that is concurrently being placed on legal hold.
                cur.execute("""
                    DELETE FROM security_audit_log
                    WHERE event_timestamp < CURRENT_TIMESTAMP - INTERVAL '1 year'
                      AND (session_id IS NULL OR session_id IN (
                          SELECT session_id FROM voting_sessions WHERE legal_hold = FALSE FOR SHARE SKIP LOCKED
                      ))
                """)
                results["audit_logs_deleted"] += cur.rowcount

            conn.commit()
            return results
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

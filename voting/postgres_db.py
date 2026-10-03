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
                        raise ValueError(f"Session {session_id} not found.")
                    
                    if session['status'] != 'ACTIVE':
                        raise ValueError(f"Session {session_id} is not ACTIVE.")
                    
                    cur.execute("SELECT CURRENT_TIMESTAMP")
                    now = cur.fetchone()['current_timestamp']
                    if now < session['start_time'] or now >= session['end_time']:
                        raise ValueError("Current time is outside the valid session window.")
                    
                    cur.execute("SELECT 1 FROM voters WHERE voter_id = %s", (voter_id,))
                    if not cur.fetchone():
                        raise ValueError(f"Voter {voter_id} not found.")
                    
                    cur.execute("SELECT 1 FROM session_voters WHERE session_id = %s AND voter_id = %s", (session_id, voter_id))
                    if not cur.fetchone():
                        raise ValueError(f"Voter {voter_id} is not eligible for session {session_id}.")
                    
                    cur.execute("SELECT status, idempotency_key, receipt_id, reservation_token, reserved_at FROM voter_participation WHERE session_id = %s AND voter_id = %s", (session_id, voter_id))
                    part = cur.fetchone()
                    
                    token = str(uuid.uuid4())
                    should_continue = False
                    if part:
                        if part['status'] == 'COMMITTED':
                            if part['idempotency_key'] == idempotency_key:
                                return False, None, part['receipt_id']
                            else:
                                raise ValueError("Voter has already cast a vote.")
                        elif part['status'] == 'PENDING':
                            res_ts = part['reserved_at']
                            if now - res_ts <= timedelta(minutes=5):
                                if part['idempotency_key'] == idempotency_key:
                                    return True, part['reservation_token'], None
                                else:
                                    raise ValueError("A vote is currently being processed.")
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
                    "VALUES (%s, %s, %s, %s, %s, %s, 'UPCOMING')",
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
            
    def update_session_status(self, session_id: str, new_status: str) -> None:
        """Open or close a session by updating its status."""
        valid_statuses = {'UPCOMING', 'ACTIVE', 'COMPLETED'}
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
                    'UPCOMING': {'ACTIVE', 'COMPLETED'},
                    'ACTIVE': {'COMPLETED'},
                    'COMPLETED': set()
                }
                
                if new_status not in valid_transitions[current_status] and current_status != new_status:
                    raise ValueError(f"Invalid transition from {current_status} to {new_status}")
                
                if current_status == 'UPCOMING' and new_status == 'ACTIVE':
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
                    cur.execute("UPDATE voting_sessions SET status = %s WHERE session_id = %s", (new_status, session_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

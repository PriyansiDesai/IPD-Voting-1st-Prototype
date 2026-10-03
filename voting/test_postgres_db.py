import unittest
import os
import uuid
import datetime
from pathlib import Path

# Do not convert database/configuration errors into skipped tests.
test_db_url = os.environ.get("TEST_DATABASE_URL")
HAS_POSTGRES = bool(test_db_url)

if HAS_POSTGRES:
    # Do not silently swallow import errors if Postgres is requested.
    import psycopg2
    from psycopg2 import OperationalError
    from voting.postgres_db import PostgresVotingRepository

    if os.environ.get("ALLOW_TEST_DB_WIPE") != "1":
        raise ValueError("Must set ALLOW_TEST_DB_WIPE=1 to explicitly confirm wiping the test database.")
    
    db_url = os.environ.get("DATABASE_URL")
    if test_db_url == db_url:
        raise ValueError("TEST_DATABASE_URL cannot be the same as DATABASE_URL.")
        
    from urllib.parse import urlparse
    parsed = urlparse(test_db_url)
    db_name = parsed.path.lstrip('/')
    if not db_name.startswith('test_ipd_'):
        raise ValueError("TEST_DATABASE_URL database name must start with 'test_ipd_'.")
        
    # Will throw if connection fails
    conn = psycopg2.connect(test_db_url)
    conn.close()

@unittest.skipUnless(HAS_POSTGRES, "PostgreSQL is unavailable; TEST_DATABASE_URL not set")
class TestPostgresVotingRepository(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ.get("TEST_DATABASE_URL")
        self.repo = PostgresVotingRepository(self.dsn)
        
        # NEVER drop public schema. Instead, drop the specific tables we own.
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    DROP TABLE IF EXISTS audit_ledger CASCADE;
                    DROP TABLE IF EXISTS ballots CASCADE;
                    DROP TABLE IF EXISTS voter_participation CASCADE;
                    DROP TABLE IF EXISTS session_choices CASCADE;
                    DROP TABLE IF EXISTS session_voters CASCADE;
                    DROP TABLE IF EXISTS voting_sessions CASCADE;
                    DROP TABLE IF EXISTS ballot_options CASCADE;
                    DROP TABLE IF EXISTS candidates CASCADE;
                    DROP TABLE IF EXISTS voters CASCADE;
                    DROP TABLE IF EXISTS schema_migrations CASCADE;
                """)
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        self.repo.run_migrations()

    def tearDown(self):
        if hasattr(self, 'repo') and self.repo.pool:
            self.repo.pool.closeall()

    def test_schema_migrations(self):
        """Test 1: Run migrations fresh, again (idempotent), and on failure."""
        # 1. Fresh database is already run in setUp.
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM schema_migrations ORDER BY version DESC")
                versions = cur.fetchall()
                self.assertTrue(len(versions) >= 2)
        finally:
            self.repo.pool.putconn(conn)
            
        # 2. Second run safely skips (should not throw)
        self.repo.run_migrations()
        
        # 3. Detect changed contents for an applied migration
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE schema_migrations SET hash = 'different' WHERE version = 1")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        with self.assertRaises(ValueError) as ctx:
            self.repo.run_migrations()
        self.assertIn("has changed since it was applied", str(ctx.exception))
        
        # Revert the hash update to 'unknown' to test legacy migration support
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                # Set version 1 to 'unknown'
                cur.execute("UPDATE schema_migrations SET hash = 'unknown' WHERE version = 1")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        # Running migrations should update the 'unknown' hash
        self.repo.run_migrations()
        
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT hash FROM schema_migrations WHERE version = 1")
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertNotEqual(row[0], 'unknown')
        finally:
            self.repo.pool.putconn(conn)
            
        # 4. Migration failure leaves no partial schema change or success record.
        import tempfile
        import os
        from pathlib import Path
        
        with tempfile.TemporaryDirectory() as tmpdirname:
            migrations_dir = Path(tmpdirname)
            
            bad_migration = migrations_dir / '999_bad_migration.sql'
            with open(bad_migration, 'w') as f:
                f.write("CREATE TABLE should_rollback (id INT);\nTHIS IS NOT VALID SQL;")
                
            try:
                with self.assertRaises(RuntimeError) as ctx:
                    self.repo.run_migrations(custom_migrations_dir=migrations_dir)
                self.assertIn("failed", str(ctx.exception))
                
                # Verify no partial schema change
                conn = self.repo.get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'should_rollback'")
                        self.assertIsNone(cur.fetchone())
                        
                        # Verify no success record
                        cur.execute("SELECT 1 FROM schema_migrations WHERE version = 999")
                        self.assertIsNone(cur.fetchone())
                finally:
                    self.repo.pool.putconn(conn)
            finally:
                pass # Temp dir cleans up automatically
            
    def _create_basic_session(self):
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status) VALUES ('S1', 'T1', 'candidate_election', '2000-01-01', '2100-01-01', 'ACTIVE')")
                sc_id = str(uuid.uuid4())
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V1', 'Alice', 'Engineering', 'Employee')")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S1', 'V1')")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C1', 'Bob')")
                cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id) VALUES (%s, 'S1', 'candidate_election', 'C1')", (sc_id,))
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

    def test_eligibility(self):
        """Test 2: Eligibility validation."""
        self._create_basic_session()
        
        # Test eligible
        self.repo.reserve_vote('S1', 'V1', str(uuid.uuid4()))
        
        # Test ineligible
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V2', 'Eve', 'HR', 'Employee')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        with self.assertRaises(ValueError) as ctx:
            self.repo.reserve_vote('S1', 'V2', str(uuid.uuid4()))
        self.assertIn("not eligible", str(ctx.exception))
    def test_choice_type(self):
        """Test 3: Choice of the correct type."""
        self._create_basic_session()
        self.assertTrue(self.repo.validate_choice('S1', 'C1'))
        self.assertFalse(self.repo.validate_choice('S1', 'O1'))
        
    def test_duplicate_voting_and_idempotency(self):
        """Test 4 & 5: Duplicate voting and same-key retry."""
        self._create_basic_session()
        ik1 = str(uuid.uuid4())
        
        # 1. Reserve normally
        s, token1, rec1 = self.repo.reserve_vote('S1', 'V1', ik1)
        self.assertTrue(s)
        self.assertIsNotNone(token1)
        self.assertIsNone(rec1)
        
        # 2. Retry with same key while pending
        s2, token2, rec2 = self.repo.reserve_vote('S1', 'V1', ik1)
        self.assertTrue(s2)
        self.assertEqual(token1, token2)
        
        # 3. Retry with different key while pending
        ik2 = str(uuid.uuid4())
        with self.assertRaises(ValueError) as ctx:
            self.repo.reserve_vote('S1', 'V1', ik2)
        self.assertIn("vote is currently being processed", str(ctx.exception))
        
        # 4. Finalize
        receipt = self.repo.finalize_vote('S1', 'V1', token1, "ENCRYPTED_DATA")
        self.assertIsNotNone(receipt)
        
        # 5. Retry with same key after finalize -> gets receipt
        s3, token3, rec3 = self.repo.reserve_vote('S1', 'V1', ik1)
        self.assertFalse(s3)
        self.assertIsNone(token3)
        self.assertEqual(rec3, receipt)
        
        # 6. Retry with different key after finalize -> Duplicate vote error
        with self.assertRaises(ValueError) as ctx:
            self.repo.reserve_vote('S1', 'V1', ik2)
        self.assertIn("already cast a vote", str(ctx.exception))
        
    def test_stale_vote_recovery(self):
        """Test stale PENDING vote recovery, lease retry, reclaim, and concurrency."""
        import datetime
        self._create_basic_session()
        ik1 = str(uuid.uuid4())
        
        # 1. Initial reservation
        s, token1, rec1 = self.repo.reserve_vote('S1', 'V1', ik1)
        self.assertTrue(s)
        self.assertIsNotNone(token1)
        
        # 2. Retry before expiry with same key (should reuse)
        s2, token2, rec2 = self.repo.reserve_vote('S1', 'V1', ik1)
        self.assertTrue(s2)
        self.assertEqual(token1, token2)
        
        # 3. Simulate expiry by manually backdating the reservation
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE voter_participation SET reserved_at = CURRENT_TIMESTAMP - INTERVAL '6 minutes' WHERE session_id = 'S1' AND voter_id = 'V1'")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        # 4. Reclaim after expiry (should issue new token)
        ik2 = str(uuid.uuid4())
        s3, token3, rec3 = self.repo.reserve_vote('S1', 'V1', ik2)
        self.assertTrue(s3)
        self.assertNotEqual(token1, token3)
        self.assertIsNotNone(token3)
        
        # 5. Old-token finalization rejected after reclaim
        with self.assertRaises(ValueError) as ctx:
            self.repo.finalize_vote('S1', 'V1', token1, "ENCRYPTED_DATA")
        self.assertIn("Reservation is stale, expired, or invalid", str(ctx.exception))
        
        # 6. Finalize with new token succeeds
        receipt = self.repo.finalize_vote('S1', 'V1', token3, "ENCRYPTED_DATA_NEW")
        self.assertIsNotNone(receipt)
        
        # 7. Concurrency Test for Reclaim
        # Set up a new expired reservation
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V2', 'Eve', 'HR', 'Employee') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S1', 'V2') ON CONFLICT DO NOTHING")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        ik_c = str(uuid.uuid4())
        self.repo.reserve_vote('S1', 'V2', ik_c)
        
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE voter_participation SET reserved_at = CURRENT_TIMESTAMP - INTERVAL '6 minutes' WHERE session_id = 'S1' AND voter_id = 'V2'")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        import threading
        barrier = threading.Barrier(2)
        
        # Two workers trying to reclaim the same expired reservation concurrently
        results = []
        errors = []
        def worker(ik):
            try:
                barrier.wait()
                s, token, rec = self.repo.reserve_vote('S1', 'V2', ik)
                results.append(token)
            except Exception as e:
                errors.append(str(e))
                
        ik_w1 = str(uuid.uuid4())
        ik_w2 = str(uuid.uuid4())
        
        t1 = threading.Thread(target=worker, args=(ik_w1,))
        t2 = threading.Thread(target=worker, args=(ik_w2,))
        
        t1.start()
        t2.start()
        
        t1.join(timeout=10)
        t2.join(timeout=10)
        
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("A vote is currently being processed.", errors[0])
        
        # 8. Test finalize_vote on an expired reservation before any reclaim
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V3', 'Charlie', 'IT', 'Admin') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S1', 'V3') ON CONFLICT DO NOTHING")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        ik_v3 = str(uuid.uuid4())
        s, token_v3, rec = self.repo.reserve_vote('S1', 'V3', ik_v3)
        self.assertTrue(s)
        
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE voter_participation SET reserved_at = CURRENT_TIMESTAMP - INTERVAL '6 minutes' WHERE session_id = 'S1' AND voter_id = 'V3'")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        # Try to finalize with the expired token before any worker reclaims it
        with self.assertRaises(ValueError) as ctx:
            self.repo.finalize_vote('S1', 'V3', token_v3, "EXPIRED_DATA")
        self.assertIn("Reservation is stale, expired, or invalid", str(ctx.exception))
        
        # Verify no ballot/audit row created for V3
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM voter_participation WHERE session_id='S1' AND voter_id='V3'")
                self.assertEqual(cur.fetchone()[0], 'PENDING')
                
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1' AND encrypted_vote_payload='EXPIRED_DATA'")
                self.assertEqual(cur.fetchone()[0], 0)
                
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id='S1' AND encrypted_vote_payload='EXPIRED_DATA'")
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self.repo.pool.putconn(conn)
    def test_rollback_on_finalization_failure(self):
        """Test 6: Rollback on finalization failure."""
        self._create_basic_session()
        ik = str(uuid.uuid4())
        s, token, rec = self.repo.reserve_vote('S1', 'V1', ik)
        
        # Try to finalize with invalid token format
        with self.assertRaises(ValueError) as ctx:
            self.repo.finalize_vote('S1', 'V1', "BAD_TOKEN", "DATA")
        self.assertIn("Invalid reservation token format", str(ctx.exception))
            
        # Ensure still pending and no ballot/audit entry added
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM voter_participation WHERE session_id='S1' AND voter_id='V1'")
                self.assertEqual(cur.fetchone()[0], 'PENDING')
                
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1'")
                self.assertEqual(cur.fetchone()[0], 0)
                
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id='S1'")
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self.repo.pool.putconn(conn)
            
        # Mid-transaction DB failure test: trigger failure after participation update and ballot insert,
        # but during audit_ledger insert, by adding a temporary check constraint.
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE audit_ledger ADD CONSTRAINT fail_test CHECK (encrypted_vote_payload != 'FAIL_ME')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        try:
            from psycopg2.errors import CheckViolation
            with self.assertRaises(CheckViolation) as ctx:
                self.repo.finalize_vote('S1', 'V1', token, "FAIL_ME")
            self.assertIn("fail_test", str(ctx.exception))
                
            # Verify transaction rolled back completely
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT status FROM voter_participation WHERE session_id='S1' AND voter_id='V1'")
                    self.assertEqual(cur.fetchone()[0], 'PENDING')
                    
                    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1' AND encrypted_vote_payload='FAIL_ME'")
                    self.assertEqual(cur.fetchone()[0], 0)
                    
                    cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id='S1' AND encrypted_vote_payload='FAIL_ME'")
                    self.assertEqual(cur.fetchone()[0], 0)
            finally:
                self.repo.pool.putconn(conn)
        finally:
            # Clean up the constraint
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("ALTER TABLE audit_ledger DROP CONSTRAINT fail_test")
                conn.commit()
            finally:
                self.repo.pool.putconn(conn)
                

    def test_postgres_read_methods(self):
        """Test 7: Postgres read methods (session lookup, choices, participation)."""
        self._create_basic_session()
        
        # Test get_session_details
        sess = self.repo.get_session_details('S1')
        self.assertIsNotNone(sess)
        self.assertEqual(sess['session_type'], 'candidate_election')
        
        # Test get_session_choices
        choices = self.repo.get_session_choices('S1')
        self.assertEqual(choices, ['C1'])
        
        # Test has_voter_voted
        self.assertFalse(self.repo.has_voter_voted('S1', 'V1'))
        
        ik = str(uuid.uuid4())
        s, token, rec = self.repo.reserve_vote('S1', 'V1', ik)
        self.repo.finalize_vote('S1', 'V1', token, "DATA")
        
        self.assertTrue(self.repo.has_voter_voted('S1', 'V1'))

    def test_postgres_voting_engine_integration(self):
        """Test 9: VotingEngine.cast_vote() full integration via PostgreSQL."""
        self._create_basic_session()
        from voting.voting_engine import VotingEngine
        
        old_db_url = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = self.dsn
            
            engine = VotingEngine(use_postgres=True)
            ik = str(uuid.uuid4())
            
            receipt = engine.cast_vote(
                session_id='S1',
                voter_id='V1',
                candidate_id='C1',
                idempotency_key=ik
            )
            self.assertIsNotNone(receipt)
            self.assertIn("receipt", receipt)
            
            # Verify participation is COMMITTED, 1 ballot, 1 audit entry
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT status FROM voter_participation WHERE session_id='S1' AND voter_id='V1'")
                    self.assertEqual(cur.fetchone()[0], 'COMMITTED')
                    
                    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1'")
                    self.assertEqual(cur.fetchone()[0], 1)
                    
                    cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id='S1'")
                    self.assertEqual(cur.fetchone()[0], 1)
            finally:
                self.repo.pool.putconn(conn)
                
            # Retry with same key on fresh instance
            engine2 = VotingEngine(use_postgres=True)
            receipt2 = engine2.cast_vote(
                session_id='S1',
                voter_id='V1',
                candidate_id='C1',
                idempotency_key=ik
            )
            self.assertEqual(receipt['receipt'], receipt2['receipt'])
            
            # Verify still 1 ballot
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1'")
                    self.assertEqual(cur.fetchone()[0], 1)
            finally:
                self.repo.pool.putconn(conn)
                
            # Close repository connections
            if hasattr(engine, 'repo') and hasattr(engine.repo, 'pool'):
                engine.repo.pool.closeall()
            if hasattr(engine2, 'repo') and hasattr(engine2.repo, 'pool'):
                engine2.repo.pool.closeall()
                
        finally:
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                del os.environ["DATABASE_URL"]

    def test_postgres_tally_unsupported(self):
        """Test 8: Postgres tallies throw NotImplementedError."""
        from voting.voting_engine import VotingEngine
        import os
        os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "dummy")
        engine = VotingEngine(use_postgres=True)
        # Mock repo to bypass connection
        class DummyRepo:
            def get_session_choices(self, sid): return []
            def has_voter_voted(self, sid, vid): return False
        engine.repo = DummyRepo()
        
        with self.assertRaises(NotImplementedError):
            engine.get_tally('S1')
            
        with self.assertRaises(NotImplementedError):
            engine.count_accepted_votes('S1')

    def test_session_management(self):
        """Test 10: Session management API (create, assign, open, prevent modifications)."""
        import datetime
        
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_TIMESTAMP")
                now = cur.fetchone()[0]
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_NEW', 'New', 'Dept', 'Role')")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C_NEW', 'New Candidate')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
            
        start = now - datetime.timedelta(hours=1)
        end = now + datetime.timedelta(hours=1)
        
        # Test validation: timezone-aware start_time < end_time
        with self.assertRaises(ValueError):
            self.repo.setup_session('S_FAIL1', 'T', 'candidate_election', datetime.datetime.now(), end, ['V_NEW'], ['C_NEW'])
            
        with self.assertRaises(ValueError):
            self.repo.setup_session('S_FAIL2', 'T', 'candidate_election', end, start, ['V_NEW'], ['C_NEW'])
            
        # Test valid creation
        self.repo.setup_session('S_NEW', 'New Title', 'candidate_election', start, end, ['V_NEW'], ['C_NEW'], 'Who?')
        
        # Test missing voters when opening
        self.repo.setup_session('S_NO_VOTERS', 'No voters', 'candidate_election', start, end, [], ['C_NEW'])
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NO_VOTERS', 'ACTIVE')
        self.assertIn("without eligible voters", str(ctx.exception))
        
        # Test missing choices when opening
        self.repo.setup_session('S_NO_CHOICES', 'No choices', 'candidate_election', start, end, ['V_NEW'], [])
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NO_CHOICES', 'ACTIVE')
        self.assertIn("without valid choices", str(ctx.exception))
        
        # Test opening outside time window (future)
        future_start = now + datetime.timedelta(hours=1)
        future_end = now + datetime.timedelta(hours=2)
        self.repo.setup_session('S_FUTURE', 'Future', 'candidate_election', future_start, future_end, ['V_NEW'], ['C_NEW'])
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_FUTURE', 'ACTIVE')
        self.assertIn("outside of its configured time window", str(ctx.exception))
        
        # Test opening outside time window (past)
        past_start = now - datetime.timedelta(hours=2)
        past_end = now - datetime.timedelta(hours=1)
        self.repo.setup_session('S_PAST', 'Past', 'candidate_election', past_start, past_end, ['V_NEW'], ['C_NEW'])
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_PAST', 'ACTIVE')
        self.assertIn("outside of its configured time window", str(ctx.exception))
        
        # Test valid transition UPCOMING -> ACTIVE
        self.repo.update_session_status('S_NEW', 'ACTIVE')
        self.assertEqual(self.repo.get_session_details('S_NEW')['status'], 'ACTIVE')
        
        # Test invalid transition ACTIVE -> UPCOMING
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NEW', 'UPCOMING')
        self.assertIn("Invalid transition", str(ctx.exception))
        
        # Test valid transition ACTIVE -> COMPLETED
        self.repo.update_session_status('S_NEW', 'COMPLETED')
        self.assertEqual(self.repo.get_session_details('S_NEW')['status'], 'COMPLETED')
        
        # Test preventing reopening a completed session (COMPLETED -> ACTIVE)
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NEW', 'ACTIVE')
        self.assertIn("Invalid transition", str(ctx.exception))
        
        # Test valid cancellation UPCOMING -> COMPLETED
        self.repo.setup_session('S_CANCEL', 'Cancel', 'candidate_election', start, end, ['V_NEW'], ['C_NEW'])
        self.repo.update_session_status('S_CANCEL', 'COMPLETED')
        self.assertEqual(self.repo.get_session_details('S_CANCEL')['status'], 'COMPLETED')
        
        # Test rollback on failure (invalid choice)
        from psycopg2.errors import ForeignKeyViolation
        with self.assertRaises(ForeignKeyViolation):
            self.repo.setup_session('S_FAIL3', 'Fail', 'candidate_election', start, end, ['V_NEW'], ['INVALID_CANDIDATE'])
        self.assertIsNone(self.repo.get_session_details('S_FAIL3'))
        
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id='S_FAIL3'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM session_choices WHERE session_id='S_FAIL3'")
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self.repo.pool.putconn(conn)
        
        # Test rollback on failure (invalid voter)
        with self.assertRaises(ForeignKeyViolation):
            self.repo.setup_session('S_FAIL4', 'Fail Voter', 'candidate_election', start, end, ['INVALID_VOTER'], ['C_NEW'])
        self.assertIsNone(self.repo.get_session_details('S_FAIL4'))
        
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id='S_FAIL4'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM session_choices WHERE session_id='S_FAIL4'")
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self.repo.pool.putconn(conn)

if __name__ == '__main__':
    unittest.main()

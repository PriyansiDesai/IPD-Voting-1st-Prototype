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
        self.repo.update_session_status('S_NO_VOTERS', 'APPROVED')
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NO_VOTERS', 'ACTIVE')
        self.assertIn("without eligible voters", str(ctx.exception))

        # Test missing choices when opening
        self.repo.setup_session('S_NO_CHOICES', 'No choices', 'candidate_election', start, end, ['V_NEW'], [])
        self.repo.update_session_status('S_NO_CHOICES', 'APPROVED')
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NO_CHOICES', 'ACTIVE')
        self.assertIn("without valid choices", str(ctx.exception))

        # Test opening outside time window (future)
        future_start = now + datetime.timedelta(hours=1)
        future_end = now + datetime.timedelta(hours=2)
        self.repo.setup_session('S_FUTURE', 'Future', 'candidate_election', future_start, future_end, ['V_NEW'], ['C_NEW'])
        self.repo.update_session_status('S_FUTURE', 'APPROVED')
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_FUTURE', 'ACTIVE')
        self.assertIn("outside of its configured time window", str(ctx.exception))

        # Test opening outside time window (past)
        past_start = now - datetime.timedelta(hours=2)
        past_end = now - datetime.timedelta(hours=1)
        self.repo.setup_session('S_PAST', 'Past', 'candidate_election', past_start, past_end, ['V_NEW'], ['C_NEW'])
        self.repo.update_session_status('S_PAST', 'APPROVED')
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_PAST', 'ACTIVE')
        self.assertIn("outside of its configured time window", str(ctx.exception))

        # Test valid transition DRAFT -> APPROVED -> ACTIVE
        self.repo.update_session_status('S_NEW', 'APPROVED')
        self.repo.update_session_status('S_NEW', 'ACTIVE')
        self.assertEqual(self.repo.get_session_details('S_NEW')['status'], 'ACTIVE')

        # Test invalid transition ACTIVE -> DRAFT
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NEW', 'DRAFT')
        self.assertIn("Invalid transition", str(ctx.exception))

        # Test valid transition ACTIVE -> COMPLETED
        self.repo.update_session_status('S_NEW', 'COMPLETED', approver_id='admin2', reason='Early end')
        self.assertEqual(self.repo.get_session_details('S_NEW')['status'], 'COMPLETED')

        # Test preventing reopening a completed session (COMPLETED -> ACTIVE)
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_session_status('S_NEW', 'ACTIVE')
        self.assertIn("Invalid transition", str(ctx.exception))

        # Test valid cancellation DRAFT -> COMPLETED
        self.repo.setup_session('S_CANCEL', 'Cancel', 'candidate_election', start, end, ['V_NEW'], ['C_NEW'])
        self.repo.update_session_status('S_CANCEL', 'COMPLETED', approver_id='admin1', reason='Cancelled early')
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

    def test_retention_and_audit(self):
        """Test the retention pruning, security audit logs, identity/ballot separation, legal holds, and shared voters."""
        import datetime
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_TIMESTAMP")
                now = cur.fetchone()[0]
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_SHARED', 'Shared', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_29', '29', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_31', '31', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_364', '364', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_366', '366', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_UNCERT', 'Uncert', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_HOLD', 'Hold', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_UNASSIGNED', 'Unassigned', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C_RET', 'Cand') ON CONFLICT DO NOTHING")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        start = now - datetime.timedelta(hours=2)
        end = now + datetime.timedelta(hours=2)

        # Helper to setup a session
        def _setup(sid, v_id, status='COMPLETED'):
            self.repo.setup_session(sid, sid, 'candidate_election', start, end, [v_id, 'V_SHARED'], ['C_RET'])
            self.repo.update_session_status(sid, 'APPROVED')
            self.repo.update_session_status(sid, 'ACTIVE')
            _, t, _ = self.repo.reserve_vote(sid, v_id, str(uuid.uuid4()))
            self.repo.finalize_vote(sid, v_id, t, f"BALLOT_{sid}")
            if status != 'ACTIVE':
                self.repo.update_session_status(sid, status, approver_id='admin', reason='Done')
                if status == 'COMPLETED':
                    self.repo.certify_session(sid, 'admin')

        _setup('S_29', 'V_29')
        _setup('S_31', 'V_31')
        _setup('S_364', 'V_364')
        _setup('S_366', 'V_366')
        _setup('S_UNCERT', 'V_UNCERT', 'ACTIVE')
        _setup('S_HOLD', 'V_HOLD')
        self.repo.toggle_legal_hold('S_HOLD', True, 'admin')

        # Modify timestamps to simulate aging
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '29 days' WHERE session_id = 'S_29'")
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '31 days' WHERE session_id = 'S_31'")
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '364 days' WHERE session_id = 'S_364'")
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '366 days' WHERE session_id = 'S_366'")
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = 'S_HOLD'")
                cur.execute("UPDATE security_audit_log SET event_timestamp = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = 'S_HOLD'")
                cur.execute("UPDATE security_audit_log SET event_timestamp = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = 'S_366'")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        import os
        original_url = os.environ.get("RETENTION_DATABASE_URL")
        os.environ["RETENTION_DATABASE_URL"] = self.repo.dsn

        results = self.repo.prune_retained_data()

        if original_url:
            os.environ["RETENTION_DATABASE_URL"] = original_url
        else:
            del os.environ["RETENTION_DATABASE_URL"]

        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                # S_29: Voter links remain
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_29'")
                self.assertEqual(cur.fetchone()[0], 2)
                cur.execute("SELECT COUNT(*) FROM voter_participation WHERE session_id = 'S_29'")
                self.assertEqual(cur.fetchone()[0], 1)

                # S_31: Voter links pruned, ballot remains
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_31'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM voter_participation WHERE session_id = 'S_31'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S_31'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id = 'S_31'")
                self.assertEqual(cur.fetchone()[0], 1)

                # S_364: Voter links pruned, ballot remains
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_364'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM voter_participation WHERE session_id = 'S_364'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S_364'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id = 'S_364'")
                self.assertEqual(cur.fetchone()[0], 1)

                # S_366: Voter links pruned, ballot pruned, audit logs pruned
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_366'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM voter_participation WHERE session_id = 'S_366'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S_366'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id = 'S_366'")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM security_audit_log WHERE session_id = 'S_366'")
                self.assertEqual(cur.fetchone()[0], 0)

                # S_UNCERT: Uncertified session retains everything
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_UNCERT'")
                self.assertEqual(cur.fetchone()[0], 2)
                cur.execute("SELECT COUNT(*) FROM voter_participation WHERE session_id = 'S_UNCERT'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S_UNCERT'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id = 'S_UNCERT'")
                self.assertEqual(cur.fetchone()[0], 1)

                # S_HOLD: Legal hold retains everything, even > 1 year
                cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_HOLD'")
                self.assertEqual(cur.fetchone()[0], 2)
                cur.execute("SELECT COUNT(*) FROM voter_participation WHERE session_id = 'S_HOLD'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S_HOLD'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM audit_ledger WHERE session_id = 'S_HOLD'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM security_audit_log WHERE session_id = 'S_HOLD'")
                self.assertGreater(cur.fetchone()[0], 0)

                # Voters: Master rows remain, including unassigned and completely pruned links
                cur.execute("SELECT COUNT(*) FROM voters")
                self.assertGreaterEqual(cur.fetchone()[0], 8)
                cur.execute("SELECT COUNT(*) FROM voters WHERE voter_id = 'V_366'")
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM voters WHERE voter_id = 'V_UNASSIGNED'")
                self.assertEqual(cur.fetchone()[0], 1)

        finally:
            self.repo.pool.putconn(conn)

    def test_database_role_permissions(self):
        """Verify that the retention role can only delete from specific tables, and the app role cannot delete."""
        import secrets
        import psycopg2
        from urllib.parse import urlparse

        conn = self.repo.get_connection()
        suffix = secrets.token_hex(4)
        app_role = f"voting_app_test_{suffix}"
        ret_role = f"voting_retention_test_{suffix}"
        password = secrets.token_urlsafe(16)

        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(f"CREATE ROLE {app_role} WITH LOGIN PASSWORD '{password}'")
                    cur.execute(f"CREATE ROLE {ret_role} WITH LOGIN PASSWORD '{password}'")
                except psycopg2.errors.InsufficientPrivilege as e:
                    conn.rollback()
                    self.skipTest(f"Insufficient privileges to test roles (missing CREATEROLE): {e}")

                # App role grants
                cur.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {app_role}")
                cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}")

                # Retention role grants
                cur.execute(f"GRANT USAGE ON SCHEMA public TO {ret_role}")
                cur.execute(f"GRANT SELECT, UPDATE (session_id) ON TABLE voting_sessions TO {ret_role}")
                cur.execute(f"GRANT SELECT, DELETE ON TABLE session_voters, voter_participation, ballots, audit_ledger, security_audit_log TO {ret_role}")
            conn.commit()

            p = urlparse(self.repo.dsn)
            app_dsn = f"postgresql://{app_role}:{password}@{p.hostname}:{p.port}{p.path}"
            ret_dsn = f"postgresql://{ret_role}:{password}@{p.hostname}:{p.port}{p.path}"

            # Test that app role CANNOT delete
            with psycopg2.connect(app_dsn) as app_conn:
                with app_conn.cursor() as app_cur:
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        app_cur.execute("DELETE FROM session_voters WHERE session_id = 'DOES_NOT_EXIST'")

            # Test that retention role CAN delete from allowed tables
            with psycopg2.connect(ret_dsn) as ret_conn:
                with ret_conn.cursor() as ret_cur:
                    ret_cur.execute("DELETE FROM session_voters WHERE session_id = 'DOES_NOT_EXIST'")

            # Test that retention role CANNOT delete from voting_sessions
            with psycopg2.connect(ret_dsn) as ret_conn:
                with ret_conn.cursor() as ret_cur:
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        ret_cur.execute("DELETE FROM voting_sessions WHERE session_id = 'DOES_NOT_EXIST'")

            # Test that retention role CANNOT delete from voters
            with psycopg2.connect(ret_dsn) as ret_conn:
                with ret_conn.cursor() as ret_cur:
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        ret_cur.execute("DELETE FROM voters WHERE voter_id = 'DOES_NOT_EXIST'")

            # Test that retention role CANNOT update legal_hold or certified_at
            with psycopg2.connect(ret_dsn) as ret_conn:
                with ret_conn.cursor() as ret_cur:
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        ret_cur.execute("UPDATE voting_sessions SET legal_hold = TRUE WHERE session_id = 'DOES_NOT_EXIST'")
                    ret_conn.rollback()
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        ret_cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP WHERE session_id = 'DOES_NOT_EXIST'")

            # Run the actual pruning operation using the temporary restricted retention role
            import datetime
            import os
            cur_time = datetime.datetime.now(datetime.timezone.utc)
            start = cur_time - datetime.timedelta(hours=2)
            end = cur_time + datetime.timedelta(hours=2)

            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_R_TEST', 'RTest', 'Dept', 'Role') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C_R_TEST', 'Cand') ON CONFLICT DO NOTHING")
            conn.commit()

            self.repo.setup_session('S_PRUNE_TEST', 'Role Test', 'candidate_election', start, end, ['V_R_TEST'], ['C_R_TEST'])
            self.repo.update_session_status('S_PRUNE_TEST', 'APPROVED')
            self.repo.update_session_status('S_PRUNE_TEST', 'ACTIVE')
            self.repo.update_session_status('S_PRUNE_TEST', 'COMPLETED', approver_id='admin', reason='Done')
            self.repo.certify_session('S_PRUNE_TEST', 'admin')

            with conn.cursor() as cur:
                cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '60 days' WHERE session_id = 'S_PRUNE_TEST'")
            conn.commit()

            original_url = os.environ.get("RETENTION_DATABASE_URL")
            os.environ["RETENTION_DATABASE_URL"] = ret_dsn
            try:
                self.repo.prune_retained_data()
            finally:
                if original_url:
                    os.environ["RETENTION_DATABASE_URL"] = original_url
                else:
                    del os.environ["RETENTION_DATABASE_URL"]

        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"DROP OWNED BY {app_role}")
                    cur.execute(f"DROP OWNED BY {ret_role}")
                    cur.execute(f"DROP ROLE IF EXISTS {app_role}")
                    cur.execute(f"DROP ROLE IF EXISTS {ret_role}")

                    cur.execute("SELECT 1 FROM pg_roles WHERE rolname IN (%s, %s)", (app_role, ret_role))
                    if cur.fetchone():
                        self.fail("Temporary roles were not successfully dropped.")
                conn.commit()
            finally:
                self.repo.pool.putconn(conn)

    def test_legal_hold_prune_race_safety(self):
        """
        Verify the locking behavior between toggle_legal_hold and prune_retained_data using actual threads.
        Test both outcomes:
        1. Hold commits before pruning locks -> Pruning skips the row, data preserved.
        2. Pruning locks before hold commits -> Hold waits, pruning commits, data is pruned, then hold is applied.
        """
        import datetime
        import time
        import threading
        import psycopg2
        import os

        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT CURRENT_TIMESTAMP")
                now = cur.fetchone()[0]
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_RACE1', 'R1', 'D', 'R') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_RACE2', 'R2', 'D', 'R') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C_RET', 'Cand') ON CONFLICT DO NOTHING")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        start = now - datetime.timedelta(hours=2)
        end = now + datetime.timedelta(hours=2)

        for sid, vid in [('S_RACE1', 'V_RACE1')]:
            self.repo.setup_session(sid, sid, 'candidate_election', start, end, [vid], ['C_RET'])
            self.repo.update_session_status(sid, 'APPROVED')
            self.repo.update_session_status(sid, 'ACTIVE')
            self.repo.update_session_status(sid, 'COMPLETED', approver_id='admin', reason='Done')
            self.repo.certify_session(sid, 'admin')

            # Age > 1 year and insert old audit log
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = %s", (sid,))
                    cur.execute("INSERT INTO security_audit_log (action_type, session_id, details) VALUES ('TEST', %s, 'old')", (sid,))
                    cur.execute("UPDATE security_audit_log SET event_timestamp = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = %s", (sid,))
                conn.commit()
            finally:
                self.repo.pool.putconn(conn)

        original_url = os.environ.get("RETENTION_DATABASE_URL")
        os.environ["RETENTION_DATABASE_URL"] = self.repo.dsn

        import psycopg2
        original_connect = psycopg2.connect
        original_log = self.repo._log_security_audit_internal

        try:
            # Case 1: Hold locks first
            hold_locked = threading.Event()
            prune_done = threading.Event()
            worker_errors = []

            def slow_log(cur, action, session_id, details):
                original_log(cur, action, session_id, details)
                if action == 'LEGAL_HOLD_PLACED' and session_id == 'S_RACE1':
                    hold_locked.set()
                    prune_done.wait(timeout=5)
            self.repo._log_security_audit_internal = slow_log

            def run_hold1():
                try:
                    self.repo.toggle_legal_hold('S_RACE1', True, 'admin')
                except Exception as e:
                    worker_errors.append(('hold1', e))
            t1 = threading.Thread(target=run_hold1)
            t1.start()

            self.assertTrue(hold_locked.wait(timeout=5), "Hold 1 didn't lock")
            try:
                self.repo.prune_retained_data() # should skip S_RACE1
            except Exception as e:
                worker_errors.append(('prune1', e))
            prune_done.set()
            t1.join(timeout=5)
            self.assertFalse(t1.is_alive(), "Thread 1 is still alive")

            if worker_errors:
                self.fail(f"Worker errors in Case 1: {worker_errors}")

            self.repo._log_security_audit_internal = original_log

            # Setup fresh session for Case 2 so it is independent of Case 1's pruning
            sid2, vid2 = 'S_RACE2_FRESH', 'V_RACE2_FRESH'
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES (%s, 'R2', 'D', 'R') ON CONFLICT DO NOTHING", (vid2,))
                conn.commit()
            finally:
                self.repo.pool.putconn(conn)

            self.repo.setup_session(sid2, sid2, 'candidate_election', start, end, [vid2], ['C_RET'])
            self.repo.update_session_status(sid2, 'APPROVED')
            self.repo.update_session_status(sid2, 'ACTIVE')
            self.repo.update_session_status(sid2, 'COMPLETED', approver_id='admin', reason='Done')
            self.repo.certify_session(sid2, 'admin')

            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE voting_sessions SET certified_at = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = %s", (sid2,))
                    cur.execute("INSERT INTO security_audit_log (action_type, session_id, details) VALUES ('TEST', %s, 'old')", (sid2,))
                    cur.execute("UPDATE security_audit_log SET event_timestamp = CURRENT_TIMESTAMP - INTERVAL '400 days' WHERE session_id = %s", (sid2,))
                conn.commit()
            finally:
                self.repo.pool.putconn(conn)

            # Case 2: Prune locks first
            prune_locked2 = threading.Event()
            hold_done2 = threading.Event()
            worker_errors2 = []

            class SlowConn:
                def __init__(self, c): self.conn = c
                def cursor(self): return self.conn.cursor()
                def commit(self):
                    prune_locked2.set()
                    hold_done2.wait(timeout=5)
                    self.conn.commit()
                def rollback(self): self.conn.rollback()
                def close(self): self.conn.close()
                def __enter__(self): return self
                def __exit__(self, a,b,c): pass

            def slow_connect(dsn):
                return SlowConn(original_connect(dsn))

            psycopg2.connect = slow_connect

            def run_prune2():
                try:
                    self.repo.prune_retained_data()
                except Exception as e:
                    worker_errors2.append(('prune2', e))
            t2 = threading.Thread(target=run_prune2)
            t2.start()

            self.assertTrue(prune_locked2.wait(timeout=5), "Prune 2 didn't lock")

            # Now run hold2 (should block until prune commits)
            def run_hold2():
                try:
                    self.repo.toggle_legal_hold(sid2, True, 'admin')
                except Exception as e:
                    worker_errors2.append(('hold2', e))
            t3 = threading.Thread(target=run_hold2)
            t3.start()

            # Use bounded database lock check to prove toggle_legal_hold is blocked
            blocked = False
            # original_url might be None, so we connect to self.repo.dsn for checking pg_locks
            for _ in range(50):
                chk_conn = original_connect(self.repo.dsn)
                try:
                    with chk_conn.cursor() as chk_cur:
                        chk_cur.execute("SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON l.pid = a.pid WHERE NOT l.granted AND a.query ILIKE '%UPDATE voting_sessions SET legal_hold%'")
                        if chk_cur.fetchone():
                            blocked = True
                            break
                finally:
                    chk_conn.close()
                time.sleep(0.1)

            self.assertTrue(blocked, "toggle_legal_hold did not block on pruning lock")

            hold_done2.set()
            t3.join(timeout=5)
            self.assertFalse(t3.is_alive(), "Thread 3 is still alive")
            t2.join(timeout=5)
            self.assertFalse(t2.is_alive(), "Thread 2 is still alive")

            if worker_errors2:
                self.fail(f"Worker errors in Case 2: {worker_errors2}")

            psycopg2.connect = original_connect

            # Assertions
            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    # Expected legal_hold state
                    cur.execute("SELECT legal_hold FROM voting_sessions WHERE session_id = 'S_RACE1'")
                    self.assertTrue(cur.fetchone()[0])
                    cur.execute("SELECT legal_hold FROM voting_sessions WHERE session_id = 'S_RACE2_FRESH'")
                    self.assertTrue(cur.fetchone()[0])

                    # S_RACE1: Preserved
                    cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_RACE1'")
                    self.assertEqual(cur.fetchone()[0], 1)
                    cur.execute("SELECT COUNT(*) FROM security_audit_log WHERE session_id = 'S_RACE1' AND event_timestamp < CURRENT_TIMESTAMP - INTERVAL '300 days'")
                    self.assertGreaterEqual(cur.fetchone()[0], 1)

                    # S_RACE2_FRESH: Pruned (since prune locked first)
                    cur.execute("SELECT COUNT(*) FROM session_voters WHERE session_id = 'S_RACE2_FRESH'")
                    self.assertEqual(cur.fetchone()[0], 0)
                    cur.execute("SELECT COUNT(*) FROM security_audit_log WHERE session_id = 'S_RACE2_FRESH' AND event_timestamp < CURRENT_TIMESTAMP - INTERVAL '300 days'")
                    self.assertEqual(cur.fetchone()[0], 0)
            finally:
                self.repo.pool.putconn(conn)

        finally:
            self.repo._log_security_audit_internal = original_log
            psycopg2.connect = original_connect

            if original_url:
                os.environ["RETENTION_DATABASE_URL"] = original_url
            else:
                if "RETENTION_DATABASE_URL" in os.environ:
                    del os.environ["RETENTION_DATABASE_URL"]

if __name__ == '__main__':
    unittest.main()

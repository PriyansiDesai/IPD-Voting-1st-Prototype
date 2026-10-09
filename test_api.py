import unittest
import os
import uuid
import datetime
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from unittest.mock import patch, MagicMock
# pyrefly: ignore [missing-import]
import jwt
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

test_private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
)
test_private_pem = test_private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)
test_public_key = test_private_key.public_key()
test_public_pem = test_public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

test_db_url = os.environ.get("TEST_DATABASE_URL")
HAS_POSTGRES = bool(test_db_url)

if HAS_POSTGRES:
    if os.environ.get("ALLOW_TEST_DB_WIPE") != "1":
        raise ValueError("Must set ALLOW_TEST_DB_WIPE=1 to explicitly confirm wiping the test database.")

    from urllib.parse import urlparse
    parsed = urlparse(test_db_url)
    db_name = parsed.path.lstrip('/')
    if db_name != 'test_ipd_voting':
        raise ValueError("TEST_DATABASE_URL database name must be exactly 'test_ipd_voting'.")

    os.environ["DATABASE_URL"] = test_db_url
    from api import app, get_current_principal, Principal, engine
    from voting.postgres_db import PostgresVotingRepository
else:
    app = None
    engine = None

client = TestClient(app) if app else None

@unittest.skipUnless(HAS_POSTGRES, "PostgreSQL is unavailable; TEST_DATABASE_URL not set")
class TestVotingAPI(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ.get("TEST_DATABASE_URL")
        self.repo = PostgresVotingRepository(self.dsn)

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
                    DROP TABLE IF EXISTS voter_identities CASCADE;
                    DROP TABLE IF EXISTS voters CASCADE;
                    DROP TABLE IF EXISTS schema_migrations CASCADE;
                """)
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        self.repo.run_migrations()

        # Insert test data
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status) VALUES ('S1', 'T1', 'candidate_election', '2000-01-01', '2100-01-01', 'DRAFT')")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V1', 'Alice', 'Engineering', 'Employee')")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S1', 'V1')")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C1', 'Bob')")
                cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id) VALUES (%s, 'S1', 'candidate_election', 'C1')", (str(uuid.uuid4()),))
                # Add mapping
                cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test-issuer', 'test-subject', 'V1')")
            conn.commit()

            # Activate S1 after inserting choices
            self.repo.update_session_status('S1', 'APPROVED', 'admin-subject')
            self.repo.update_session_status('S1', 'ACTIVE', 'admin-subject')
        finally:
            self.repo.pool.putconn(conn)

        # Reset dependency override before each test
        app.dependency_overrides = {}

    def tearDown(self):
        if hasattr(self, "repo") and self.repo.pool:
            if not self.repo.pool.closed:
                self.repo.pool.closeall()
        # Make sure API engine's connections are closed
        if engine and engine.repo and engine.repo.pool:
            if not engine.repo.pool.closed:
                engine.repo.pool.closeall()
            engine.repo = PostgresVotingRepository(self.dsn)

    def test_missing_authentication(self):
        # Should get 401 because get_current_principal raises it by default
        ik = str(uuid.uuid4())
        response = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 401)
        self.assertIn("Not authenticated", response.json()["detail"])

    def test_unknown_identity(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="unknown-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik = str(uuid.uuid4())
        response = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Unknown identity", response.json()["detail"])

    def test_successful_mapped_vote(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik = str(uuid.uuid4())
        response = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("receipt", data)

    def test_duplicate_vote(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik1 = str(uuid.uuid4())
        # First vote
        res1 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik1})
        self.assertEqual(res1.status_code, 200)
        receipt1 = res1.json().get("receipt")

        # Retry with same key -> returns original receipt
        res2 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik1})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json().get("receipt"), receipt1)

    def test_vote_endpoint_logging_privacy(self):
        """
        Exercise the real authenticated POST /vote endpoint and ensure no sensitive
        data (voter ID, choice ID, encoded bits) is logged.
        """
        import logging
        import uuid

        # 1. Create a dedicated test session in DRAFT, add its choices and voter eligibility, then activate it
        session_id = "S_PRIVACY"
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                # Insert session in DRAFT
                cur.execute("INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status) VALUES (%s, 'Privacy Test', 'candidate_election', '2000-01-01', '2100-01-01', 'DRAFT')", (session_id,))

                # Insert voter and eligibility
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_PRIVACY', 'Privacy Test Voter', 'Engineering', 'Employee') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES (%s, 'V_PRIVACY')", (session_id,))

                # Insert identity mapping
                cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('priv-issuer', 'priv-sub', 'V_PRIVACY')")

                # Insert 6 choices to ensure a 3-bit encoding
                for i in range(1, 7):
                    cand_id = f"C_PRIV_{i}"
                    cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (cand_id, f"PrivCand{i}"))
                    cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id) VALUES (%s, %s, 'candidate_election', %s)", (str(uuid.uuid4()), session_id, cand_id))
            conn.commit()

            # Activate the session
            self.repo.update_session_status(session_id, "APPROVED", "admin-sub")
            self.repo.update_session_status(session_id, "ACTIVE", "admin-sub")
        finally:
            self.repo.pool.putconn(conn)

        def override_principal():
            from api import Principal
            return Principal(issuer="priv-issuer", subject="priv-sub")

        from api import get_current_principal
        app.dependency_overrides[get_current_principal] = override_principal

        ik = str(uuid.uuid4())

        # 2. Capture records with a custom logging handler that allows an empty log list
        class ListHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.log_records = []
            def emit(self, record):
                self.log_records.append(self.format(record))

        custom_handler = ListHandler()
        root_logger = logging.getLogger()
        old_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(custom_handler)

        try:
            response = client.post(
                "/vote",
                json={"session_id": session_id, "candidate_id": "C_PRIV_6"},
                headers={"Idempotency-Key": ik}
            )
        finally:
            root_logger.removeHandler(custom_handler)
            root_logger.setLevel(old_level)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        # 3. Assert no record contains the distinctive choice ID, expected multi-bit encoding, or voter ID
        # Expected encoded bits for C_PRIV_6 (index 5 out of 6 candidates) is '101'
        expected_encoded_bits = "101"
        distinctive_choice = "C_PRIV_6"
        voter_id = "V_PRIVACY"

        log_text = "\n".join(custom_handler.log_records)

        self.assertNotIn(distinctive_choice, log_text)
        self.assertNotIn(expected_encoded_bits, log_text)
        self.assertNotIn(voter_id, log_text)


    def test_unknown_session(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik = str(uuid.uuid4())
        response = client.post("/vote", json={"session_id": "UNKNOWN", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"])

    def test_m3_encoding_failure_reclaim(self):
        """
        Test that a vote failing during M3 encoding leaves participation PENDING,
        and that a retry after 5 minutes successfully reclaims the reservation.
        """
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik1 = str(uuid.uuid4())

        # 1. First vote request (fails during encode_choice)
        with patch('voting.voting_engine.encode_choice') as mock_encode_choice:
            mock_encode_choice.side_effect = ValueError("Deterministic encoding exception")
            res1 = client.post(
                "/vote",
                json={"session_id": "S1", "candidate_id": "C1"},
                headers={"Idempotency-Key": ik1}
            )
            self.assertEqual(res1.status_code, 422)
            self.assertIn("Cryptographic pipeline failed: Deterministic encoding exception", res1.json()["detail"])

        # 2. Verify participation is PENDING and no ballot exists
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM voter_participation WHERE session_id = 'S1' AND voter_id = 'V1'")
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], 'PENDING')

                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S1'")
                count = cur.fetchone()[0]
                self.assertEqual(count, 0)

                # 3. Simulate 6 minutes passing by updating reserved_at
                cur.execute("UPDATE voter_participation SET reserved_at = CURRENT_TIMESTAMP - INTERVAL '6 minutes' WHERE session_id = 'S1' AND voter_id = 'V1'")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        # 4. Retry with a NEW Idempotency-Key (encoder is no longer mocked)
        ik2 = str(uuid.uuid4())
        res2 = client.post(
            "/vote",
            json={"session_id": "S1", "candidate_id": "C1"},
            headers={"Idempotency-Key": ik2}
        )

        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["status"], "success")

        # 5. Verify participation is COMMITTED and exactly one ballot exists
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM voter_participation WHERE session_id = 'S1' AND voter_id = 'V1'")
                row = cur.fetchone()
                self.assertEqual(row[0], 'COMMITTED')

                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S1'")
                count = cur.fetchone()[0]
                self.assertEqual(count, 1)
        finally:
            self.repo.pool.putconn(conn)

    def test_m4_bb84_abort_reclaim(self):
        """
        Test that a vote failing during M4 BB84 (run_secure_bb84) returning an aborted result
        leaves participation PENDING, and that no ballot is finalized. Also asserts that encrypt_vote
        is not called. It then proves that a retry after 6 minutes successfully reclaims the reservation
        and results in a COMMITTED state with exactly one ballot.
        Note: The crypto mock is limited to the first failure to trigger the abort deterministically.
        """
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik1 = str(uuid.uuid4())

        # 1. First vote request (fails during run_secure_bb84)
        with patch('voting.voting_engine.run_secure_bb84') as mock_bb84, \
             patch('voting.voting_engine.encrypt_vote') as mock_encrypt:

            mock_bb84.return_value = {"secure": False, "aborted": True, "reason": "Test Abort"}

            res1 = client.post(
                "/vote",
                json={"session_id": "S1", "candidate_id": "C1"},
                headers={"Idempotency-Key": ik1}
            )
            self.assertEqual(res1.status_code, 422)
            self.assertIn("Cryptographic pipeline failed", res1.json()["detail"])
            self.assertIn("Test Abort", res1.json()["detail"])

            mock_encrypt.assert_not_called()

        # 2. Verify participation is PENDING and no ballot exists
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM voter_participation WHERE session_id = 'S1' AND voter_id = 'V1'")
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], 'PENDING')

                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S1'")
                count = cur.fetchone()[0]
                self.assertEqual(count, 0)

                # 3. Simulate 6 minutes passing by updating reserved_at
                cur.execute("UPDATE voter_participation SET reserved_at = CURRENT_TIMESTAMP - INTERVAL '6 minutes' WHERE session_id = 'S1' AND voter_id = 'V1'")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        # 4. Retry with a NEW Idempotency-Key (mock crypto for a deterministic successful retry)
        ik2 = str(uuid.uuid4())
        with patch('voting.voting_engine.run_secure_bb84') as mock_bb84_retry, \
             patch('voting.voting_engine.encrypt_vote') as mock_encrypt_retry:

            mock_bb84_retry.return_value = {
                "secure": True,
                "aborted": False,
                "final_key": [1] * 256
            }
            mock_encrypt_retry.return_value = b"test_ciphertext"

            res2 = client.post(
                "/vote",
                json={"session_id": "S1", "candidate_id": "C1"},
                headers={"Idempotency-Key": ik2}
            )

        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["status"], "success")

        # 5. Verify participation is COMMITTED and exactly one ballot exists
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM voter_participation WHERE session_id = 'S1' AND voter_id = 'V1'")
                row = cur.fetchone()
                self.assertEqual(row[0], 'COMMITTED')

                cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id = 'S1'")
                count = cur.fetchone()[0]
                self.assertEqual(count, 1)
        finally:
            self.repo.pool.putconn(conn)

    def test_ineligible_voter(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject-2")
        app.dependency_overrides[get_current_principal] = override_principal

        # Create the mapping for V2
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V2', 'Eve', 'HR', 'Employee')")
                cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test-issuer', 'test-subject-2', 'V2')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        ik = str(uuid.uuid4())
        # V2 is not in session_voters for S1
        response = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 403)
        self.assertIn("not eligible", response.json()["detail"])

    def test_invalid_choice(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        ik = str(uuid.uuid4())
        response = client.post("/vote", json={"session_id": "S1", "candidate_id": "UNKNOWN_CANDIDATE"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Choice not valid", response.json()["detail"])

    def test_get_sessions_eligible_voter(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.get("/sessions")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("sessions", data)
        self.assertEqual(len(data["sessions"]), 1)
        self.assertEqual(data["sessions"][0]["session_id"], "S1")

    def test_get_sessions_unmapped_identity(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="unknown-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.get("/sessions")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Unknown identity", response.json()["detail"])

    def test_get_sessions_no_eligible_sessions(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject-2")
        app.dependency_overrides[get_current_principal] = override_principal

        # Create the mapping for V2
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V2', 'Eve', 'HR', 'Employee')")
                cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test-issuer', 'test-subject-2', 'V2')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        response = client.get("/sessions")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("sessions", data)
        self.assertEqual(len(data["sessions"]), 0)

    def test_get_sessions_outside_window_excluded(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        # Create the mapping for V1 to two new sessions, one before start, one after end
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status) VALUES ('S_PAST', 'Past', 'candidate_election', '2000-01-01', '2001-01-01', 'ACTIVE')")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S_PAST', 'V1')")

                cur.execute("INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status) VALUES ('S_FUTURE', 'Future', 'candidate_election', '2100-01-01', '2101-01-01', 'ACTIVE')")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S_FUTURE', 'V1')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        response = client.get("/sessions")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("sessions", data)

        # Should only contain S1 (from setUp), not S_PAST or S_FUTURE
        session_ids = [s["session_id"] for s in data["sessions"]]
        self.assertIn("S1", session_ids)
        self.assertNotIn("S_PAST", session_ids)
        self.assertNotIn("S_FUTURE", session_ids)

    def test_get_session_details_assigned_voter(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.get("/sessions/S1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["session_id"], "S1")
        self.assertIn("choices", data)
        self.assertEqual(data["choices"], ["C1"])

    def test_get_session_details_unmapped_identity(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="unknown-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.get("/sessions/S1")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Unknown identity", response.json()["detail"])

    def test_get_session_details_unassigned_voter(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject-2")
        app.dependency_overrides[get_current_principal] = override_principal

        # Create the mapping for V2
        conn = self.repo.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V2', 'Eve', 'HR', 'Employee')")
                cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test-issuer', 'test-subject-2', 'V2')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)

        response = client.get("/sessions/S1")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found or not assigned", response.json()["detail"])

    def test_get_session_details_unknown_session(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.get("/sessions/UNKNOWN_SESSION")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found or not assigned", response.json()["detail"])

    def test_postgres_commit_and_recreate_engine(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        import api
        original_engine = api.engine

        try:
            ik1 = str(uuid.uuid4())
            # Cast a vote
            response = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik1})
            self.assertEqual(response.status_code, 200)

            # Close engine pool and recreate engine
            if api.engine and api.engine.repo and api.engine.repo.pool:
                api.engine.repo.pool.closeall()
            from voting.voting_engine import VotingEngine
            api.engine = VotingEngine(use_postgres=True)

            # Confirm participation remains committed (second vote rejected)
            ik2 = str(uuid.uuid4())
            response2 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik2})
            self.assertEqual(response2.status_code, 409)
            self.assertIn("already cast a vote", response2.json()["detail"])
        finally:
            if api.engine and api.engine.repo and api.engine.repo.pool:
                api.engine.repo.pool.closeall()
            api.engine = original_engine
            if get_current_principal in app.dependency_overrides:
                del app.dependency_overrides[get_current_principal]

    def test_log_privacy(self):
        import logging
        import sys
        from io import StringIO

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger()
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)

        stdout_stream = StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_stream

        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        try:
            ik = str(uuid.uuid4())
            response = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
            self.assertEqual(response.status_code, 200)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            sys.stdout = old_stdout

        logs = log_stream.getvalue()
        prints = stdout_stream.getvalue()

        self.assertNotIn("C1", logs, "Plaintext candidate_id found in logs!")
        self.assertNotIn("Bob", logs, "Plaintext candidate name found in logs!")
        self.assertNotIn("C1", prints, "Plaintext candidate_id found in prints!")
        self.assertNotIn("Bob", prints, "Plaintext candidate name found in prints!")

    def test_admin_routes_forbidden_for_voter(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject", permissions=[])
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.post("/admin/sessions/S1/status", json={"status": "COMPLETED"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Admin permission required", response.json()["detail"])

    def test_admin_update_session_status(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="admin-subject", permissions=["admin"])
        app.dependency_overrides[get_current_principal] = override_principal

        # ACTIVE -> COMPLETED is valid
        response = client.post("/admin/sessions/S1/status", json={"status": "COMPLETED", "reason": "Testing"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

    def test_admin_certify_session(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="admin-subject", permissions=["admin"])
        app.dependency_overrides[get_current_principal] = override_principal

        # Must be COMPLETED first to be certified
        client.post("/admin/sessions/S1/status", json={"status": "COMPLETED", "reason": "Testing"})

        response = client.post("/admin/sessions/S1/certify")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

    def test_admin_toggle_legal_hold(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="admin-subject", permissions=["admin"])
        app.dependency_overrides[get_current_principal] = override_principal

        response = client.post("/admin/sessions/S1/legal-hold", json={"active": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

    @patch('api.OIDC_ISSUER', 'test-issuer')
    @patch('api.OIDC_AUDIENCE', 'test-audience')
    @patch('api.jwks_client')
    def test_real_jwt_admin_authorized(self, mock_jwks_client):
        # Setup mock jwks client to return our public key
        mock_key = MagicMock()
        mock_key.key = test_public_pem
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_key

        # Create valid JWT
        payload = {
            "iss": "test-issuer",
            "sub": "admin-subject",
            "aud": "test-audience",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
            "permissions": ["admin"]
        }
        token = jwt.encode(payload, test_private_pem, algorithm="RS256")

        # Make request to admin route
        response = client.post(
            "/admin/sessions/S1/status",
            json={"status": "COMPLETED", "reason": "Testing JWT"},
            headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

    @patch('api.OIDC_ISSUER', 'test-issuer')
    @patch('api.OIDC_AUDIENCE', 'test-audience')
    @patch('api.jwks_client')
    def test_real_jwt_admin_forbidden_for_user(self, mock_jwks_client):
        # Setup mock jwks client
        mock_key = MagicMock()
        mock_key.key = test_public_pem
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_key

        # Create valid JWT without "admin" permission
        payload = {
            "iss": "test-issuer",
            "sub": "user-subject",
            "aud": "test-audience",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
            "permissions": ["read"] # No admin
        }
        token = jwt.encode(payload, test_private_pem, algorithm="RS256")

        response = client.post(
            "/admin/sessions/S1/status",
            json={"status": "COMPLETED", "reason": "Testing JWT"},
            headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Admin permission required", response.json()["detail"])



    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    def test_m4_auth_provider_failure_reclaim(self, mock_run_secure, mock_encrypt):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal

        import os
        from voting.classical_channel import MAX_FRAMES
        req_len = 16 + MAX_FRAMES * 16

        call_count = [0]
        generated_key = [None]
        def flappy_provider(length):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Provider failed on first call")
            else:
                key = os.urandom(length)
                generated_key[0] = key
                return key

        original_provider = engine.auth_key_provider
        engine.auth_key_provider = flappy_provider
        try:
            # 1. First /vote request
            ik1 = str(uuid.uuid4())
            response1 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik1})

            self.assertEqual(response1.status_code, 422)
            self.assertIn("Cryptographic pipeline failed", response1.json()["detail"])

            mock_run_secure.assert_not_called()
            mock_encrypt.assert_not_called()

            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT status FROM voter_participation WHERE session_id='S1' AND voter_id='V1'")
                    row = cur.fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row[0], 'PENDING')

                    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1'")
                    self.assertEqual(cur.fetchone()[0], 0)

                    cur.execute("UPDATE voter_participation SET reserved_at = CURRENT_TIMESTAMP - INTERVAL '6 minutes' WHERE session_id='S1' AND voter_id='V1'")
                conn.commit()
            finally:
                self.repo.pool.putconn(conn)

            # 3. Retry with new IK
            ik2 = str(uuid.uuid4())
            mock_run_secure.return_value = {"secure": True, "aborted": False, "final_key": [0]*256}
            mock_encrypt.return_value = b"test_ciphertext"

            response2 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik2})
            self.assertEqual(response2.status_code, 200)

            self.assertEqual(mock_encrypt.call_count, 1)

            self.assertEqual(mock_run_secure.call_count, 1)
            self.assertEqual(mock_run_secure.call_args[1].get("auth_key"), generated_key[0])

            conn = self.repo.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT status FROM voter_participation WHERE session_id='S1' AND voter_id='V1'")
                    self.assertEqual(cur.fetchone()[0], 'COMMITTED')

                    cur.execute("SELECT COUNT(*) FROM ballots WHERE session_id='S1'")
                    self.assertEqual(cur.fetchone()[0], 1)
            finally:
                self.repo.pool.putconn(conn)
        finally:
            engine.auth_key_provider = original_provider


class TestAdminCheckAuth(unittest.TestCase):
    @patch.dict(os.environ, {"DEV_ADMIN_CHECK_ENABLED": "1"})
    def test_admin_check_auth(self):
        from api import app, get_current_principal, Principal
        # pyrefly: ignore [missing-import]
        from fastapi.testclient import TestClient
        client = TestClient(app)
        def override_principal():
            return Principal(issuer='test-issuer', subject='admin-subject', permissions=['admin'])
        app.dependency_overrides[get_current_principal] = override_principal
        response = client.get('/admin/check-auth')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')

    @patch.dict(os.environ, {"DEV_ADMIN_CHECK_ENABLED": "1"})
    def test_admin_check_auth_forbidden_for_user(self):
        from api import app, get_current_principal, Principal
        # pyrefly: ignore [missing-import]
        from fastapi.testclient import TestClient
        client = TestClient(app)
        def override_principal():
            return Principal(issuer='test-issuer', subject='test-subject', permissions=[])
        app.dependency_overrides[get_current_principal] = override_principal
        response = client.get('/admin/check-auth')
        self.assertEqual(response.status_code, 403)

    @patch.dict(os.environ, {"DEV_ADMIN_CHECK_ENABLED": "1"})
    def test_admin_check_auth_missing_authentication(self):
        from api import app, get_current_principal, Principal
        # pyrefly: ignore [missing-import]
        from fastapi.testclient import TestClient
        client = TestClient(app)
        if get_current_principal in app.dependency_overrides:
            del app.dependency_overrides[get_current_principal]
        response = client.get('/admin/check-auth')
        self.assertEqual(response.status_code, 401)

    def test_admin_check_auth_disabled_flag(self):
        from api import app, get_current_principal, Principal
        # pyrefly: ignore [missing-import]
        from fastapi.testclient import TestClient
        client = TestClient(app)
        def override_principal():
            return Principal(issuer='test-issuer', subject='admin-subject', permissions=['admin'])
        app.dependency_overrides[get_current_principal] = override_principal

        # Test when not set
        with patch.dict(os.environ, {}, clear=True):
            response = client.get('/admin/check-auth')
            self.assertEqual(response.status_code, 404)

        # Test when set to 0
        with patch.dict(os.environ, {"DEV_ADMIN_CHECK_ENABLED": "0"}):
            response = client.get('/admin/check-auth')
            self.assertEqual(response.status_code, 404)


class TestDefaultEngineAuthProvider(unittest.TestCase):
    def setUp(self):
        import api
        from unittest.mock import patch, MagicMock
        
        self.original_engine = getattr(api, 'engine', None)
        self.original_overrides = getattr(api.app, 'dependency_overrides', {})
        
        # We set up a mocked engine for the request tests.
        # This isolates request behavior testing from startup wiring testing.
        from voting.voting_engine import VotingEngine
        import os
        
        self.original_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "dummy"
        
        with patch('voting.postgres_db.PostgresVotingRepository'):
            api.engine = VotingEngine(use_postgres=True, auth_key_provider=api.simulation_auth_key_provider)
            
        api.engine.repo = MagicMock()
        api.engine.repo.get_voter_id_by_identity.return_value = 'V1'
        api.engine.repo.get_voter_session_details.return_value = {
            "session_id": "S1", "title": "T1", "status": "ACTIVE",
            "session_type": "candidate_election", "start_time": "2000-01-01",
            "end_time": "2100-01-01", "choices": ["C1"]
        }
        api.engine.repo.validate_choice.return_value = True
        api.engine.repo.reserve_vote.return_value = (True, "mock_token", None)
        api.engine.repo.get_session_choices.return_value = ["C1", "C2"]
        api.engine.repo.finalize_vote.return_value = {"tx_id": "mock_tx"}

    def tearDown(self):
        import api
        import os
        api.engine = self.original_engine
        api.app.dependency_overrides = self.original_overrides
        if self.original_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_db_url

    @patch('api.VotingEngine')
    def test_m4_default_api_startup_wiring(self, mock_voting_engine):
        """Verify the engine actually created by api.py at startup uses the provider."""
        import api
        
        # Call the default engine builder
        engine = api.create_default_engine()
        
        # Verify it was constructed with the correct arguments
        mock_voting_engine.assert_called_once_with(
            use_postgres=True,
            auth_key_provider=api.simulation_auth_key_provider
        )

    @patch('voting.voting_engine.encrypt_vote')
    @patch('voting.voting_engine.run_secure_bb84')
    def test_m4_default_api_provider_fresh_keys(self, mock_bb84, mock_encrypt):
        """Prove the API request logic uses the provider and gives fresh keys (isolated from startup)."""
        import api
        from fastapi.testclient import TestClient
        import uuid
        
        self.assertIsNotNone(api.engine.auth_key_provider)
        
        client = TestClient(api.app)
        
        def mock_get_current_principal():
            from api import Principal
            return Principal(issuer='test-issuer', subject='V1')
            
        api.app.dependency_overrides[api.get_current_principal] = mock_get_current_principal
        
        mock_bb84.return_value = {"secure": True, "aborted": False, "final_key": [0]*256}
        mock_encrypt.return_value = b"fake_ciphertext"
        
        ik1 = str(uuid.uuid4())
        resp1 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik1})
        self.assertEqual(resp1.status_code, 200, resp1.text)
        
        ik2 = str(uuid.uuid4())
        resp2 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik2})
        self.assertEqual(resp2.status_code, 200, resp2.text)
        
        self.assertEqual(mock_bb84.call_count, 2)
        
        key1 = mock_bb84.call_args_list[0][1].get("auth_key")
        key2 = mock_bb84.call_args_list[1][1].get("auth_key")
        
        self.assertIsNotNone(key1)
        self.assertIsNotNone(key2)
        self.assertNotEqual(key1, key2)
        self.assertGreaterEqual(len(key1), 16 + 4700 * 16)
        self.assertGreaterEqual(len(key2), 16 + 4700 * 16)

    def test_m4_default_api_provider_failure_prevents_ballot(self):
        """Prove provider failure during request prevents ballot creation (isolated from startup)."""
        import api
        from fastapi.testclient import TestClient
        import uuid
        
        def failing_provider(req_len):
            raise RuntimeError("Simulation failure")
            
        api.engine.auth_key_provider = failing_provider
        
        client = TestClient(api.app)
        
        def mock_get_current_principal():
            from api import Principal
            return Principal(issuer='test-issuer', subject='V1')
            
        api.app.dependency_overrides[api.get_current_principal] = mock_get_current_principal
        
        ik = str(uuid.uuid4())
        resp = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("Cryptographic pipeline failed", resp.json()["detail"])
        
        api.engine.repo.finalize_vote.assert_not_called()

if __name__ == '__main__':
    import unittest
    unittest.main()

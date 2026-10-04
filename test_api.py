import unittest
import os
import uuid
import datetime
from fastapi.testclient import TestClient

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
                cur.execute("INSERT INTO voting_sessions (session_id, title, session_type, start_time, end_time, status) VALUES ('S1', 'T1', 'candidate_election', '2000-01-01', '2100-01-01', 'ACTIVE')")
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V1', 'Alice', 'Engineering', 'Employee')")
                cur.execute("INSERT INTO session_voters (session_id, voter_id) VALUES ('S1', 'V1')")
                cur.execute("INSERT INTO candidates (candidate_id, candidate_name) VALUES ('C1', 'Bob')")
                cur.execute("INSERT INTO session_choices (session_choice_id, session_id, session_type, candidate_id) VALUES (%s, 'S1', 'candidate_election', 'C1')", (str(uuid.uuid4()),))
                # Add mapping
                cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test-issuer', 'test-subject', 'V1')")
            conn.commit()
        finally:
            self.repo.pool.putconn(conn)
        
        # Reset dependency override before each test
        app.dependency_overrides = {}

    def tearDown(self):
        if hasattr(self, 'repo') and self.repo.pool:
            self.repo.pool.closeall()
        # Make sure API engine's connections are closed
        if engine and engine.repo and engine.repo.pool:
            engine.repo.pool.closeall()
            engine.repo = PostgresVotingRepository(self.dsn) # refresh for next test

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

        # Retry with new key -> Conflict
        ik2 = str(uuid.uuid4())
        res3 = client.post("/vote", json={"session_id": "S1", "candidate_id": "C1"}, headers={"Idempotency-Key": ik2})
        self.assertEqual(res3.status_code, 409)
        self.assertIn("already cast a vote", res3.json()["detail"])

    def test_unknown_session(self):
        def override_principal():
            return Principal(issuer="test-issuer", subject="test-subject")
        app.dependency_overrides[get_current_principal] = override_principal
        
        ik = str(uuid.uuid4())
        response = client.post("/vote", json={"session_id": "UNKNOWN", "candidate_id": "C1"}, headers={"Idempotency-Key": ik})
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"])

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

if __name__ == "__main__":
    unittest.main()

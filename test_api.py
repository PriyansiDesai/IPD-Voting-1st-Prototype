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

if __name__ == "__main__":
    unittest.main()
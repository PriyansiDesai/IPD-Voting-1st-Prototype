import unittest
import os
import datetime
from pathlib import Path
import psycopg2

test_db_url = os.environ.get("TEST_DATABASE_URL")
HAS_POSTGRES = bool(test_db_url)

if HAS_POSTGRES:
    from voting.admin_provisioning_repo import AdminProvisioningRepository
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

@unittest.skipUnless(HAS_POSTGRES, "PostgreSQL is unavailable; TEST_DATABASE_URL not set")
class TestAdminProvisioning(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ.get("TEST_DATABASE_URL")
        # Ensure fresh state
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    DROP TABLE IF EXISTS voter_identities CASCADE;
                    DROP TABLE IF EXISTS provisioning_tokens CASCADE;
                    DROP TABLE IF EXISTS voters CASCADE;
                    DROP TABLE IF EXISTS schema_migrations CASCADE;
                """)
            conn.commit()
        finally:
            conn.close()

        # Run standard migrations to setup tables
        setup_repo = PostgresVotingRepository(self.dsn)
        setup_repo.run_migrations()
        
        # Insert a test voter
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO voters (voter_id, name, department, role) VALUES ('V_TEST_PROV', 'Alice', 'Engineering', 'Voter')")
            conn.commit()
        finally:
            conn.close()

        self.admin_repo = AdminProvisioningRepository(self.dsn)

    def test_successful_linking(self):
        raw_token = self.admin_repo.generate_token('V_TEST_PROV')
        
        # Verify only hash is stored
        import hashlib
        expected_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT token_hash FROM provisioning_tokens WHERE voter_id = 'V_TEST_PROV'")
                stored_hash = cur.fetchone()[0]
                self.assertEqual(stored_hash, expected_hash)
                self.assertNotEqual(stored_hash, raw_token)
        finally:
            conn.close()

        linked_voter_id = self.admin_repo.consume_token_and_link(raw_token, 'https://auth.example.com/', 'sub-123')
        self.assertEqual(linked_voter_id, 'V_TEST_PROV')
        
        # Verify mapping exists
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT voter_id FROM voter_identities WHERE issuer = %s AND subject = %s", ('https://auth.example.com/', 'sub-123'))
                self.assertEqual(cur.fetchone()[0], 'V_TEST_PROV')
        finally:
            conn.close()

    def test_expired_token(self):
        raw_token = self.admin_repo.generate_token('V_TEST_PROV', expires_in_minutes=-10)
        with self.assertRaisesRegex(ValueError, "Token has expired"):
            self.admin_repo.consume_token_and_link(raw_token, 'https://auth.example.com/', 'sub-456')

    def test_replay_token(self):
        raw_token = self.admin_repo.generate_token('V_TEST_PROV')
        # First use
        self.admin_repo.consume_token_and_link(raw_token, 'https://auth.example.com/', 'sub-789')
        # Second use
        with self.assertRaisesRegex(ValueError, "Token has already been consumed"):
            self.admin_repo.consume_token_and_link(raw_token, 'https://auth.example.com/', 'sub-999')

    def test_uniqueness(self):
        raw_token1 = self.admin_repo.generate_token('V_TEST_PROV')
        raw_token2 = self.admin_repo.generate_token('V_TEST_PROV')
        
        self.admin_repo.consume_token_and_link(raw_token1, 'https://auth.example.com/', 'sub-unique')
        
        with self.assertRaisesRegex(ValueError, "Identity mapping already exists"):
            self.admin_repo.consume_token_and_link(raw_token2, 'https://auth.example.com/', 'sub-unique')

    def test_role_privileges(self):
        import uuid
        test_id = str(uuid.uuid4()).replace('-', '_')
        app_role = f"voting_app_test_{test_id}"
        admin_role = f"voting_admin_test_{test_id}"
        
        conn = psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                # Setup roles for the test
                cur.execute(f"CREATE ROLE {app_role}")
                cur.execute(f"CREATE ROLE {admin_role}")
                
                # Apply voting_app simulated grants (as per DATABASE_ROLES.md)
                cur.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {app_role}")
                cur.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE voter_identities FROM {app_role}")
                cur.execute(f"REVOKE ALL PRIVILEGES ON TABLE provisioning_tokens FROM {app_role}")
                
                # Apply voting_admin simulated grants
                cur.execute(f"GRANT SELECT ON voters TO {admin_role}")
                cur.execute(f"GRANT SELECT, INSERT, UPDATE ON provisioning_tokens TO {admin_role}")
                cur.execute(f"GRANT SELECT, INSERT ON voter_identities TO {admin_role}")
                
            conn.commit()
            
            # Test voting_app restrictions
            with conn.cursor() as cur:
                cur.execute(f"SET ROLE {app_role}")
                
                # Should not be able to read provisioning_tokens
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute("SELECT * FROM provisioning_tokens")
                conn.rollback()

                cur.execute(f"SET ROLE {app_role}")
                # Should not be able to insert voter_identities
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test', 'test', 'V_TEST_PROV')")
                conn.rollback()
                
            # Test voting_admin privileges
            with conn.cursor() as cur:
                cur.execute(f"SET ROLE {admin_role}")
                # Should be able to read provisioning_tokens
                cur.execute("SELECT * FROM provisioning_tokens")
                # Should be able to insert voter_identities
                try:
                    cur.execute("INSERT INTO voter_identities (issuer, subject, voter_id) VALUES ('test_admin', 'test_admin', 'V_TEST_PROV')")
                except psycopg2.errors.UniqueViolation:
                    pass
                conn.rollback()
                
        finally:
            conn.rollback()  # Rollback any open transaction from the tests
            with conn.cursor() as cur:
                cur.execute("RESET ROLE")
                # Safely remove only these roles' privileges and objects in this database
                cur.execute(f"DROP OWNED BY {app_role} CASCADE")
                cur.execute(f"DROP OWNED BY {admin_role} CASCADE")
                cur.execute(f"DROP ROLE {app_role}")
                cur.execute(f"DROP ROLE {admin_role}")
                
                # Confirm both generated roles have been removed
                cur.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN (%s, %s)", (app_role, admin_role))
                if cur.fetchone():
                    raise RuntimeError("Cleanup failed: temporary roles were not fully removed.")
            conn.commit()
            conn.close()

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

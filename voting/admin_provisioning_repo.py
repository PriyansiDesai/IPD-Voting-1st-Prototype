import secrets
import hashlib
from datetime import datetime, timedelta, timezone
import psycopg2
from typing import Optional

class AdminProvisioningRepository:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _get_connection(self):
        return psycopg2.connect(self.dsn)

    def generate_token(self, voter_id: str, expires_in_minutes: int = 60) -> str:
        """Generates a high-entropy provisioning token, stores its hash, and returns the raw token."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                # Validate voter exists
                cur.execute("SELECT 1 FROM voters WHERE voter_id = %s", (voter_id,))
                if not cur.fetchone():
                    raise ValueError(f"Unknown voter: {voter_id}")
                
                cur.execute(
                    "INSERT INTO provisioning_tokens (token_hash, voter_id, expires_at) VALUES (%s, %s, %s)",
                    (token_hash, voter_id, expires_at)
                )
            conn.commit()
            return raw_token
        finally:
            conn.close()

    def consume_token_and_link(self, raw_token: str, issuer: str, subject: str) -> str:
        """Consumes a provisioning token and atomicly links the voter identity."""
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT voter_id, expires_at, consumed_at FROM provisioning_tokens WHERE token_hash = %s FOR UPDATE",
                    (token_hash,)
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Invalid provisioning token")
                
                voter_id, expires_at, consumed_at = row
                
                if consumed_at is not None:
                    raise ValueError("Token has already been consumed")
                
                if expires_at < datetime.now(timezone.utc):
                    raise ValueError("Token has expired")
                
                # Check for existing identity mapping constraints
                try:
                    cur.execute(
                        "INSERT INTO voter_identities (issuer, subject, voter_id) VALUES (%s, %s, %s)",
                        (issuer, subject, voter_id)
                    )
                except psycopg2.errors.UniqueViolation:
                    raise ValueError("Identity mapping already exists")
                
                # Mark as consumed
                cur.execute(
                    "UPDATE provisioning_tokens SET consumed_at = CURRENT_TIMESTAMP WHERE token_hash = %s",
                    (token_hash,)
                )
                
            conn.commit()
            return voter_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

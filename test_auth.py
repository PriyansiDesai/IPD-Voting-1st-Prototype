import unittest
import os
import uuid
import time
from unittest import mock
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from fastapi.testclient import TestClient

# Generate an RSA key pair for testing
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend()
)
public_key = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()
)

public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

# A fake JWK Client that returns our test public key
class DummySigningKey:
    def __init__(self, key):
        self.key = key

class DummyJWKClient:
    def __init__(self, url):
        self.url = url
    def get_signing_key_from_jwt(self, token):
        return DummySigningKey(public_pem)

# Set env vars before importing api
os.environ["OIDC_ISSUER"] = "https://test-issuer.com"
os.environ["OIDC_AUDIENCE"] = "test-audience"
os.environ["OIDC_JWKS_URL"] = "https://test-issuer.com/.well-known/jwks.json"

with mock.patch("jwt.PyJWKClient", DummyJWKClient):
    import api
    client = TestClient(api.app)

class TestAuth(unittest.TestCase):
    def setUp(self):
        # We need to mock the engine for some tests
        self.mock_repo = mock.Mock()
        
        # Keep original config
        self.orig_issuer = api.OIDC_ISSUER
        self.orig_audience = api.OIDC_AUDIENCE
        self.orig_jwks_client = api.jwks_client
        api.OIDC_ISSUER = "https://test-issuer.com"
        api.OIDC_AUDIENCE = "test-audience"
        api.jwks_client = DummyJWKClient("https://test-issuer.com/.well-known/jwks.json")

        self.engine_patcher = mock.patch("api.engine")
        self.mock_engine = self.engine_patcher.start()
        self.mock_engine.repo = self.mock_repo

    def tearDown(self):
        self.engine_patcher.stop()
        api.OIDC_ISSUER = self.orig_issuer
        api.OIDC_AUDIENCE = self.orig_audience
        api.jwks_client = self.orig_jwks_client

    def generate_token(self, payload_overrides=None, use_wrong_key=False):
        now = int(time.time())
        payload = {
            "iss": "https://test-issuer.com",
            "sub": "user-123",
            "aud": "test-audience",
            "exp": now + 3600,
            "nbf": now - 60
        }
        if payload_overrides:
            payload.update(payload_overrides)
        
        key = private_pem
        if use_wrong_key:
            wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
            key = wrong_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            )
            
        return jwt.encode(payload, key, algorithm="RS256")

    def test_missing_oidc_config(self):
        api.OIDC_ISSUER = None
        token = self.generate_token()
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 500)
        self.assertIn("OIDC is not configured", res.json()["detail"])

    def test_valid_signed_token(self):
        self.mock_repo.get_voter_id_by_identity.return_value = "V1"
        self.mock_engine.cast_vote.return_value = {"status": "success", "receipt": "abc"}
        
        token = self.generate_token()
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.mock_repo.get_voter_id_by_identity.assert_called_with("https://test-issuer.com", "user-123")

    def test_invalid_signature(self):
        token = self.generate_token(use_wrong_key=True)
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 401)
        self.assertIn("Authentication failed", res.json()["detail"])

    def test_wrong_issuer(self):
        token = self.generate_token({"iss": "https://wrong-issuer.com"})
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 401)

    def test_wrong_audience(self):
        token = self.generate_token({"aud": "wrong-audience"})
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 401)

    def test_expired_token(self):
        now = int(time.time())
        token = self.generate_token({"exp": now - 3600})
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 401)

    def test_nbf_in_future(self):
        now = int(time.time())
        token = self.generate_token({"nbf": now + 3600})
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 401)

    def test_no_voter_identities_mapping(self):
        self.mock_repo.get_voter_id_by_identity.return_value = None
        token = self.generate_token()
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        res = client.post("/vote", json={"session_id": "S1"}, headers=headers)
        self.assertEqual(res.status_code, 403)
        self.assertIn("Unknown identity", res.json()["detail"])

if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Must mock env vars before importing provisioning_api
os.environ["OIDC_ISSUER"] = "https://auth.example.com/"
os.environ["OIDC_AUDIENCE"] = "test-audience"
os.environ["OIDC_JWKS_URL"] = "https://auth.example.com/.well-known/jwks.json"
os.environ["ADMIN_DATABASE_URL"] = "postgresql://test_db"

from provisioning_api import app

class TestProvisioningApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch('provisioning_api.jwt.decode')
    @patch('provisioning_api.PyJWKClient.get_signing_key_from_jwt')
    @patch('provisioning_api.AdminProvisioningRepository.consume_token_and_link')
    def test_successful_provisioning(self, mock_consume, mock_get_key, mock_decode):
        mock_decode.return_value = {"sub": "sub-123"}
        mock_consume.return_value = "V1"
        
        response = self.client.post("/provision", 
            json={"provisioning_token": "valid-token"},
            headers={"Authorization": "Bearer good-jwt"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success", "voter_id": "V1"})
        mock_consume.assert_called_once_with("valid-token", "https://auth.example.com/", "sub-123")

    @patch('provisioning_api.PyJWKClient.get_signing_key_from_jwt')
    def test_invalid_auth0_token(self, mock_get_key):
        mock_get_key.side_effect = Exception("Invalid signature")
        
        response = self.client.post("/provision", 
            json={"provisioning_token": "valid-token"},
            headers={"Authorization": "Bearer bad-jwt"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication failed"})

    @patch('provisioning_api.jwt.decode')
    @patch('provisioning_api.PyJWKClient.get_signing_key_from_jwt')
    @patch('provisioning_api.AdminProvisioningRepository.consume_token_and_link')
    def test_provisioning_token_failure(self, mock_consume, mock_get_key, mock_decode):
        mock_decode.return_value = {"sub": "sub-123"}
        mock_consume.side_effect = ValueError("Token has expired")
        
        response = self.client.post("/provision", 
            json={"provisioning_token": "expired-token"},
            headers={"Authorization": "Bearer good-jwt"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Token has expired"})

    @patch('provisioning_api.jwt.decode')
    @patch('provisioning_api.PyJWKClient.get_signing_key_from_jwt')
    @patch('provisioning_api.AdminProvisioningRepository.consume_token_and_link')
    def test_jwt_decode_parameters(self, mock_consume, mock_get_key, mock_decode):
        # Setup mock return to avoid authentication failure
        mock_decode.return_value = {"sub": "sub-123"}
        mock_consume.return_value = "V1"
        
        mock_key = MagicMock()
        mock_key.key = "mocked-signing-key"
        mock_get_key.return_value = mock_key
        
        response = self.client.post("/provision", 
            json={"provisioning_token": "valid-token"},
            headers={"Authorization": "Bearer good-jwt"}
        )
        
        # Verify jwt.decode was called with correct parameters
        mock_decode.assert_called_once_with(
            "good-jwt",
            "mocked-signing-key",
            algorithms=["RS256"],
            audience="test-audience",
            issuer="https://auth.example.com/",
            options={"require": ["exp", "iss", "sub", "aud"]}
        )

    @patch('provisioning_api.jwt.decode')
    @patch('provisioning_api.PyJWKClient.get_signing_key_from_jwt')
    def test_jwt_decode_raises_401(self, mock_get_key, mock_decode):
        # Setup mock to simulate PyJWT raising an error (e.g. ExpiredSignatureError)
        import jwt
        mock_decode.side_effect = jwt.ExpiredSignatureError("Signature has expired")
        
        response = self.client.post("/provision", 
            json={"provisioning_token": "valid-token"},
            headers={"Authorization": "Bearer expired-jwt"}
        )
        
        # Verify 401 is returned
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication failed"})

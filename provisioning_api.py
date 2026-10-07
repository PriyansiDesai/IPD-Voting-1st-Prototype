import os
# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, Field

from voting.admin_provisioning_repo import AdminProvisioningRepository

app = FastAPI(title="Identity Provisioning API")

OIDC_ISSUER = os.environ.get("OIDC_ISSUER")
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE")
OIDC_JWKS_URL = os.environ.get("OIDC_JWKS_URL")

jwks_client = PyJWKClient(OIDC_JWKS_URL) if OIDC_JWKS_URL else None
security = HTTPBearer(auto_error=False)

class Principal(BaseModel):
    issuer: str
    subject: str

def get_current_principal(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Principal:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    if not OIDC_ISSUER or not OIDC_AUDIENCE or not jwks_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC is not configured"
        )
    
    token = credentials.credentials
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=OIDC_AUDIENCE,
            issuer=OIDC_ISSUER,
            options={"require": ["exp", "iss", "sub", "aud"]}
        )
        subject = payload.get("sub")
        if not subject:
            raise ValueError("Missing 'sub' claim")
        
        return Principal(issuer=OIDC_ISSUER, subject=subject)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )

class ProvisioningRequest(BaseModel):
    provisioning_token: str = Field(..., description="The single-use provisioning token")

@app.post("/provision")
def provision_identity(
    request: ProvisioningRequest,
    principal: Principal = Depends(get_current_principal)
):
    dsn = os.environ.get("ADMIN_DATABASE_URL")
    if not dsn:
        raise HTTPException(status_code=500, detail="Database not configured")
        
    repo = AdminProvisioningRepository(dsn)
    
    try:
        voter_id = repo.consume_token_and_link(
            request.provisioning_token, 
            principal.issuer, 
            principal.subject
        )
        return {"status": "success", "voter_id": voter_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

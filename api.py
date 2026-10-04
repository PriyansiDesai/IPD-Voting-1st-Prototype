from typing import Any, Dict, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uuid
import os
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, Field

from voting.voting_engine import (
    VotingEngine,
    UnknownSessionError,
    InactiveSessionError,
    UnknownVoterError,
    IneligibleVoterError,
    DuplicateVoteError,
    UnknownCandidateError,
    CandidateNotAssignedError,
    InvalidChoiceError,
    UnknownOptionError,
    OptionNotAssignedError,
    VotingError,
)

app = FastAPI(title="Voting M2 API")

# Instantiate a global engine; using PostgreSQL backed engine as requested.
# The endpoint should be synchronous to avoid event loop issues with psycopg2.
try:
    engine = VotingEngine(use_postgres=True)
except Exception as e:
    engine = None
    print(f"Failed to initialize PostgreSQL VotingEngine: {e}")

class Principal(BaseModel):
    issuer: str
    subject: str

security = HTTPBearer(auto_error=False)

OIDC_ISSUER = os.environ.get("OIDC_ISSUER")
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE")
OIDC_JWKS_URL = os.environ.get("OIDC_JWKS_URL")

jwks_client = PyJWKClient(OIDC_JWKS_URL) if OIDC_JWKS_URL else None

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

class VoteRequest(BaseModel):
    session_id: str = Field(..., description="The session ID to vote in")
    choice_id: Optional[str] = None
    candidate_id: Optional[str] = None
    option_id: Optional[str] = None

@app.post("/vote")
def cast_vote_endpoint(
    request: VoteRequest,
    idempotency_key: uuid.UUID = Header(alias="Idempotency-Key"),
    principal: Principal = Depends(get_current_principal)
):
    if not engine or not getattr(engine, 'repo', None):
        raise HTTPException(status_code=500, detail="Database not initialized")

    # Map the authenticated principal to an internal voter_id
    voter_id = engine.repo.get_voter_id_by_identity(principal.issuer, principal.subject)
    if not voter_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown identity"
        )

    try:
        # Call the existing synchronous engine method
        result = engine.cast_vote(
            session_id=request.session_id,
            voter_id=voter_id,
            choice_id=request.choice_id,
            candidate_id=request.candidate_id,
            option_id=request.option_id,
            idempotency_key=str(idempotency_key)
        )
        return result
    except UnknownSessionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (UnknownVoterError, IneligibleVoterError) as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DuplicateVoteError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (
        InvalidChoiceError,
        UnknownOptionError,
        OptionNotAssignedError,
        UnknownCandidateError,
        CandidateNotAssignedError,
    ) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except InactiveSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except VotingError as e:
        # Other base domain errors generally map to 422 if it's a validation error
        # like "Multiple vote choices supplied" or similar parameter errors.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

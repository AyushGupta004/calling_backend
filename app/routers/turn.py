import base64
import hashlib
import hmac
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import IceServer, TurnCredentialsResponse

logger = logging.getLogger("calling.turn")

router = APIRouter(tags=["TURN / ICE"])

# Default configuration with fallbacks for local development/testing
DEFAULT_STUN_URL = "stun:stun.l.google.com:19302"
DEFAULT_TURN_HOST = "turn.example.com"
DEFAULT_STATIC_AUTH_SECRET = "calling-backend-turn-secret-key"
DEFAULT_TTL = 3600


def is_turn_configured(turn_host: Optional[str] = None) -> bool:
    """
    Check if a real TURN host is configured.
    Returns False if turn_host is empty, whitespace, or equals the placeholder 'turn.example.com'.
    """
    host = (turn_host if turn_host is not None else os.environ.get("TURN_HOST", DEFAULT_TURN_HOST)).strip()
    return bool(host and host.lower() != DEFAULT_TURN_HOST.lower())


def generate_coturn_rest_credentials(
    user_id: str,
    secret: str,
    turn_host: str,
    ttl: int = DEFAULT_TTL,
    stun_url: str = DEFAULT_STUN_URL,
    turn_configured: Optional[bool] = None,
) -> TurnCredentialsResponse:
    """
    Generate short-lived Coturn REST API (draft-uberti-behave-turn-rest-00) credentials.
    Username format: {expiry_timestamp}:{user_id}
    Credential format: base64(hmac-sha1(secret, username))
    """
    expiry_timestamp = int(time.time()) + ttl
    username = f"{expiry_timestamp}:{user_id}"

    key = secret.encode("utf-8")
    message = username.encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha1).digest()
    credential = base64.b64encode(digest).decode("utf-8")

    ice_servers = [
        IceServer(urls=stun_url),
        IceServer(
            urls=[
                f"turn:{turn_host}:3478?transport=udp",
                f"turn:{turn_host}:3478?transport=tcp",
                f"turns:{turn_host}:5349?transport=tcp",
            ],
            username=username,
            credential=credential,
        ),
    ]

    if turn_configured is None:
        turn_configured = is_turn_configured(turn_host)

    return TurnCredentialsResponse(
        iceServers=ice_servers,
        ttl=ttl,
        turnConfigured=turn_configured,
    )


@router.get("/turn-credentials", response_model=TurnCredentialsResponse)
@router.get("/turn-credentials/", response_model=TurnCredentialsResponse, include_in_schema=False)
@router.get("/api/turn-credentials", response_model=TurnCredentialsResponse, include_in_schema=False)
@router.get("/api/turn-credentials/", response_model=TurnCredentialsResponse, include_in_schema=False)
def get_turn_credentials(
    user_id: Optional[str] = Query(None, description="Authenticated requesting user ID"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id", description="Authenticated requesting user ID header"),
    ttl: Optional[int] = Query(None, ge=60, le=86400, description="Optional custom TTL in seconds (60-86400)"),
    db: Session = Depends(get_db),
):
    """
    Issue short-lived TURN credentials for WebRTC ICE negotiation behind NAT/firewalls.
    Requires the caller to be authenticated as a known registered user.
    """
    auth_user_id = (user_id or x_user_id or "").strip()
    if not auth_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: 'user_id' query parameter or 'X-User-Id' header must be provided",
        )

    # Validate caller is a known user in the database (reusing users.py / contacts.py convention)
    user = db.query(User).filter(User.id == auth_user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{auth_user_id}' not found",
        )

    turn_host = os.environ.get("TURN_HOST", DEFAULT_TURN_HOST).strip()
    secret = os.environ.get("TURN_STATIC_AUTH_SECRET", DEFAULT_STATIC_AUTH_SECRET).strip()
    stun_url = os.environ.get("STUN_URL", DEFAULT_STUN_URL).strip()

    env_ttl_str = os.environ.get("TURN_TTL")
    resolved_ttl = ttl or (int(env_ttl_str) if env_ttl_str and env_ttl_str.isdigit() else DEFAULT_TTL)

    turn_configured = is_turn_configured(turn_host)
    if not turn_configured:
        logger.warning(
            "Issuing TURN credentials with placeholder TURN_HOST ('%s'). WebRTC media relay will fail.",
            turn_host,
        )

    return generate_coturn_rest_credentials(
        user_id=user.id,
        secret=secret,
        turn_host=turn_host,
        ttl=resolved_ttl,
        stun_url=stun_url,
        turn_configured=turn_configured,
    )


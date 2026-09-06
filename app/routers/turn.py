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


import socket

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

    If turn_configured is False, returns STUN-only in iceServers so WebRTC clients
    do not attempt connection to broken/unreachable placeholder TURN servers.
    """
    if turn_configured is None:
        turn_configured = is_turn_configured(turn_host)

    # Base ICE servers with STUN (always safe and functional)
    ice_servers = [
        IceServer(urls=stun_url),
    ]

    # Only append TURN servers if a real, non-placeholder TURN host is configured
    if turn_configured:
        expiry_timestamp = int(time.time()) + ttl
        username = f"{expiry_timestamp}:{user_id}"

        key = secret.encode("utf-8")
        message = username.encode("utf-8")
        digest = hmac.new(key, message, hashlib.sha1).digest()
        credential = base64.b64encode(digest).decode("utf-8")

        ice_servers.append(
            IceServer(
                urls=[
                    f"turn:{turn_host}:3478?transport=udp",
                    f"turn:{turn_host}:3478?transport=tcp",
                    f"turns:{turn_host}:5349?transport=tcp",
                ],
                username=username,
                credential=credential,
            )
        )

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
            "[TURN-CREDENTIALS] STUN-ONLY issued for user '%s' (TURN_HOST='%s' is unconfigured/placeholder). turnConfigured=false",
            auth_user_id,
            turn_host,
        )
    else:
        logger.info(
            "[TURN-CREDENTIALS] WORKING TURN+STUN issued for user '%s' (TURN_HOST='%s'). turnConfigured=true",
            auth_user_id,
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


@router.get("/turn-check")
@router.get("/turn-check/", include_in_schema=False)
@router.get("/api/turn-check", include_in_schema=False)
def check_turn_status():
    """
    Diagnostic endpoint to verify whether the backend has a real TURN server configured
    and perform a quick UDP reachability test to port 3478.
    """
    turn_host = os.environ.get("TURN_HOST", DEFAULT_TURN_HOST).strip()
    turn_configured = is_turn_configured(turn_host)

    response = {
        "turn_configured": turn_configured,
        "turn_host": turn_host,
        "default_placeholder": DEFAULT_TURN_HOST,
        "stun_url": os.environ.get("STUN_URL", DEFAULT_STUN_URL).strip(),
    }

    if not turn_configured:
        response["status"] = "unconfigured"
        response["message"] = (
            f"TURN_HOST is currently '{turn_host}' (default placeholder). "
            "Clients will receive STUN-only in GET /turn-credentials. "
            "Configure TURN_HOST and TURN_STATIC_AUTH_SECRET in your environment or Render dashboard."
        )
        return response

    # Attempt a quick UDP STUN binding probe to port 3478
    udp_reachable = False
    probe_note = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        # RFC 5389 STUN Binding Request (20 bytes: 0x0001 type, 0 length, 0x2112A442 cookie, 12-byte id)
        stun_binding_req = (
            b"\x00\x01\x00\x00"
            b"\x21\x12\xa4\x42"
            b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c"
        )
        sock.sendto(stun_binding_req, (turn_host, 3478))
        data, _ = sock.recvfrom(512)
        if len(data) >= 20:
            udp_reachable = True
            probe_note = "Received STUN response from TURN server on UDP port 3478"
        sock.close()
    except socket.timeout:
        probe_note = "UDP socket timeout (port 3478 did not reply within 2.0s; check firewall/security group)"
    except Exception as exc:
        probe_note = f"Socket error: {exc}"

    response["status"] = "healthy" if udp_reachable else "degraded"
    response["udp_port_3478_probe"] = {
        "reachable": udp_reachable,
        "note": probe_note,
    }
    return response



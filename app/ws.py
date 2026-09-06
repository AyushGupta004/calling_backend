import json
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.connection_manager import manager
from app.database import SessionLocal
from app.models import User

logger = logging.getLogger("signaling.ws")


router = APIRouter(tags=["Signaling WebSocket"])


@router.websocket("/ws/{user_id}")
async def websocket_signaling_endpoint(
    websocket: WebSocket,
    user_id: str,
):
    """
    WebSocket endpoint /ws/{user_id} for WebRTC signaling and call state management.
    Registers user on connect (skips presence broadcast).
    """
    user_id = user_id.strip()
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="user_id is required")
        return

    # On connect: register user as online (skipping online presence broadcast)
    await manager.connect(user_id, websocket)

    active_call_id: Optional[str] = None
    call_partner_id: Optional[str] = None

    try:
        while True:
            raw_data = await websocket.receive_text()

            # Robust JSON parsing: do not crash on malformed payloads
            try:
                message = json.loads(raw_data)
            except Exception:
                logger.warning(f"Dropped malformed JSON from user {user_id}")
                continue

            if not isinstance(message, dict):
                continue

            msg_type = message.get("type")
            if not msg_type:
                continue

            # Heartbeat ping/pong support
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            # ---------------------------------------------------------
            # 1. "call_request" — {type, to_user_id, from_user_id, caller_name}
            # ---------------------------------------------------------
            if msg_type in ("call_request", "call-request"):
                to_user_id = message.get("to_user_id") or message.get("target_user_id")
                if not to_user_id:
                    continue

                # Check: is the caller already busy?
                if manager.is_busy(user_id):
                    logger.info(f"[Signaling] system -> {user_id} | type='call_failed' | reason='busy'")
                    await manager.send_to(user_id, {"type": "call_failed", "reason": "busy"})
                    continue

                # Check: is to_user_id online?
                if not manager.is_online(to_user_id):
                    logger.info(f"[Signaling] system -> {user_id} | type='call_failed' | reason='offline'")
                    await manager.send_to(user_id, {"type": "call_failed", "reason": "offline"})
                    continue

                # Check: is to_user_id already busy?
                if manager.is_busy(to_user_id):
                    logger.info(f"[Signaling] system -> {user_id} | type='call_failed' | reason='busy'")
                    await manager.send_to(user_id, {"type": "call_failed", "reason": "busy"})
                    continue

                # Generate call_id and mark BOTH users as busy
                call_id = str(uuid.uuid4())
                manager.set_call_participants(user_id, to_user_id, call_id)
                active_call_id = call_id
                call_partner_id = to_user_id

                # Extract or query caller_name
                caller_name = message.get("caller_name")
                if not caller_name:
                    try:
                        with SessionLocal() as db:
                            user_record = db.query(User).filter(User.id == user_id).first()
                            if user_record and user_record.name:
                                caller_name = user_record.name
                    except Exception as db_err:
                        logger.debug(f"Could not query caller name for user {user_id}: {db_err}")

                incoming_payload = {
                    "type": "incoming_call",
                    "from_user_id": user_id,
                    "call_id": call_id,
                }
                if caller_name:
                    incoming_payload["caller_name"] = caller_name

                logger.info(
                    f"[Signaling] {user_id} -> {to_user_id} | type='incoming_call' | call_id='{call_id}'"
                    + (f" | caller_name='{caller_name}'" if caller_name else "")
                )

                # Relay to receiver
                delivered = await manager.send_to(to_user_id, incoming_payload)
                if not delivered:
                    # In case receiver disconnected in the exact race window
                    manager.end_call(call_id)
                    active_call_id = None
                    call_partner_id = None
                    logger.info(f"[Signaling] system -> {user_id} | type='call_failed' | reason='offline'")
                    await manager.send_to(user_id, {"type": "call_failed", "reason": "offline"})

            # ---------------------------------------------------------
            # 2. "call_accepted" — {type, call_id, to_user_id}
            # ---------------------------------------------------------
            elif msg_type in ("call_accepted", "call-accepted", "call-accept"):
                call_id = message.get("call_id") or active_call_id
                to_user_id = message.get("to_user_id") or message.get("target_user_id") or call_partner_id

                if call_id and to_user_id:
                    active_call_id = call_id
                    call_partner_id = to_user_id
                    logger.info(f"[Signaling] {user_id} -> {to_user_id} | type='call_accepted' | call_id='{call_id}'")
                    # Relay to caller
                    await manager.send_to(
                        to_user_id,
                        {
                            "type": "call_accepted",
                            "call_id": call_id,
                        },
                    )

            # ---------------------------------------------------------
            # 3. "call_rejected" — {type, call_id, to_user_id}
            # ---------------------------------------------------------
            elif msg_type in ("call_rejected", "call-rejected", "call-reject"):
                call_id = message.get("call_id") or active_call_id
                to_user_id = message.get("to_user_id") or message.get("target_user_id") or call_partner_id

                if call_id and to_user_id:
                    logger.info(f"[Signaling] {user_id} -> {to_user_id} | type='call_rejected' | call_id='{call_id}'")
                    # Relay to caller
                    await manager.send_to(
                        to_user_id,
                        {
                            "type": "call_rejected",
                            "call_id": call_id,
                        },
                    )

                # Clear busy status for both users tied to this call_id
                if call_id:
                    manager.end_call(call_id)
                manager.clear_busy(user_id)
                active_call_id = None
                call_partner_id = None

            # ---------------------------------------------------------
            # 4. "call_ended" — {type, call_id, to_user_id}
            # ---------------------------------------------------------
            elif msg_type in ("call_ended", "call-ended", "hang-up", "hang_up"):
                call_id = message.get("call_id") or active_call_id or manager.get_busy_call_id(user_id)
                to_user_id = message.get("to_user_id") or message.get("target_user_id") or call_partner_id or manager.get_call_partner(user_id)

                if call_id and to_user_id:
                    logger.info(f"[Signaling] {user_id} -> {to_user_id} | type='call_ended' | call_id='{call_id}'")
                    # Relay to the other party
                    await manager.send_to(
                        to_user_id,
                        {
                            "type": "call_ended",
                            "call_id": call_id,
                        },
                    )

                # Clear busy status for both users tied to this call_id
                if call_id:
                    manager.end_call(call_id)
                manager.clear_busy(user_id)
                active_call_id = None
                call_partner_id = None

            # ---------------------------------------------------------
            # 5. "offer" / "answer" / "ice-candidate" (aliases: "ice_candidate", "candidate")
            # ---------------------------------------------------------
            elif msg_type in ("offer", "answer", "ice_candidate", "ice-candidate", "candidate"):
                call_id = message.get("call_id") or active_call_id
                to_user_id = message.get("to_user_id") or message.get("target_user_id") or call_partner_id

                # Validate the sender is actually part of an active call with this call_id
                user_busy_call = manager.get_busy_call_id(user_id)
                if not user_busy_call or user_busy_call != call_id:
                    logger.warning(
                        f"Ignored signaling '{msg_type}' from {user_id}: not part of active call '{call_id}'"
                    )
                    continue

                if to_user_id:
                    logger.info(f"[Signaling] {user_id} -> {to_user_id} | type='{msg_type}' | call_id='{call_id}'")
                    # Relay full payload directly to that user's active socket
                    await manager.send_to(to_user_id, message)

            else:
                logger.info(f"Unrecognized message type '{msg_type}' from user '{user_id}'")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user: {user_id}")
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
    finally:
        # If user was in an active call, send call_ended to call partner
        call_id = manager.get_busy_call_id(user_id) or active_call_id
        partner_id = manager.get_call_partner(user_id) or call_partner_id

        if call_id and partner_id:
            logger.info(f"[Signaling] system -> {partner_id} | type='call_ended' | reason='peer_disconnected' | call_id='{call_id}'")
            await manager.send_to(
                partner_id,
                {
                    "type": "call_ended",
                    "reason": "peer_disconnected",
                    "call_id": call_id,
                },
            )
            manager.end_call(call_id)


        # Clear busy status and remove from ConnectionManager
        manager.clear_busy(user_id)
        manager.disconnect(user_id, websocket)

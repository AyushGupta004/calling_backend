import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket


logger = logging.getLogger("signaling.manager")


@dataclass
class Call:
    call_id: str
    caller_id: str
    receiver_id: str
    state: str = "ringing"


class ConnectionManager:
    """In-memory WebSocket registry and one-to-one call state."""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.calls: Dict[str, Call] = {}
        self.user_call_ids: Dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info(
            "[ConnectionManager] User '%s' connected (total online: %s)",
            user_id,
            len(self.active_connections),
        )

    def is_current_connection(self, user_id: str, websocket: WebSocket) -> bool:
        return self.active_connections.get(user_id) is websocket

    def disconnect(self, user_id: str, websocket: Optional[WebSocket] = None):
        current = self.active_connections.get(user_id)
        if current is None or (websocket is not None and current is not websocket):
            return
        del self.active_connections[user_id]
        logger.info("[ConnectionManager] User '%s' disconnected", user_id)

    async def send_to(self, user_id: str, message: Dict[str, Any]) -> bool:
        websocket = self.active_connections.get(user_id)
        if websocket is None:
            return False

        try:
            await websocket.send_text(json.dumps(message))
            return True
        except Exception as exc:
            logger.warning("[ConnectionManager] Failed to send to '%s': %s", user_id, exc)
            self.disconnect(user_id, websocket)
            return False

    def is_online(self, user_id: str) -> bool:
        return user_id in self.active_connections

    def get_online_users(self) -> List[str]:
        return list(self.active_connections)

    def get_call(self, call_id: str) -> Optional[Call]:
        return self.calls.get(call_id)

    def get_user_call(self, user_id: str) -> Optional[Call]:
        call_id = self.user_call_ids.get(user_id)
        return self.calls.get(call_id) if call_id else None

    def is_busy(self, user_id: str) -> bool:
        return user_id in self.user_call_ids

    def get_busy_call_id(self, user_id: str) -> Optional[str]:
        return self.user_call_ids.get(user_id)

    def get_call_partner(self, user_id: str) -> Optional[str]:
        call = self.get_user_call(user_id)
        if call is None:
            return None
        return call.receiver_id if call.caller_id == user_id else call.caller_id

    async def create_call(self, caller_id: str, receiver_id: str) -> Tuple[Optional[Call], Optional[str]]:
        async with self.lock:
            if not self.is_online(receiver_id):
                return None, "offline"
            if self.is_busy(caller_id) or self.is_busy(receiver_id):
                return None, "busy"

            call = Call(
                call_id=str(uuid.uuid4()),
                caller_id=caller_id,
                receiver_id=receiver_id,
            )
            self.calls[call.call_id] = call
            self.user_call_ids[caller_id] = call.call_id
            self.user_call_ids[receiver_id] = call.call_id
            return call, None

    def is_participant(self, call_id: str, user_id: str) -> bool:
        call = self.get_call(call_id)
        return bool(call and user_id in (call.caller_id, call.receiver_id))

    def set_state(self, call_id: str, state: str) -> bool:
        call = self.get_call(call_id)
        if call is None:
            return False
        call.state = state
        return True

    def end_call(self, call_id: str) -> Optional[Call]:
        call = self.calls.pop(call_id, None)
        if call is None:
            return None
        for user_id in (call.caller_id, call.receiver_id):
            if self.user_call_ids.get(user_id) == call_id:
                del self.user_call_ids[user_id]
        return call

    async def broadcast(self, message: Dict[str, Any], exclude_user: Optional[str] = None):
        for user_id, websocket in list(self.active_connections.items()):
            if user_id == exclude_user:
                continue
            try:
                await websocket.send_text(json.dumps(message))
            except Exception:
                self.disconnect(user_id, websocket)


manager = ConnectionManager()

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from fastapi import WebSocket

logger = logging.getLogger("signaling.manager")


class ConnectionManager:
    """
    In-memory registry managing active WebSocket connections and call busy-states.
    Holds all real-time state in memory with no database writes for presence.
    """

    def __init__(self):
        # user_id (str) -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}

        # user_id (str) -> busy_call_id (str or None)
        # Enforces: users cannot participate in multiple active calls simultaneously
        self.busy_status: Dict[str, Optional[str]] = {}

        # Concurrency lock to prevent race conditions during call setup
        self.lock = asyncio.Lock()


    async def connect(self, user_id: str, websocket: WebSocket):
        """
        Accept and register an incoming WebSocket connection.
        If the user previously had a connection, the new socket takes over.
        """
        await websocket.accept()
        self.active_connections[user_id] = websocket
        if user_id not in self.busy_status:
            self.busy_status[user_id] = None

        logger.info(f"[ConnectionManager] User '{user_id}' connected (Total online: {len(self.active_connections)})")

    def disconnect(self, user_id: str, websocket: Optional[WebSocket] = None):
        """
        Remove user from connection registry and clear any active call busy state.
        Accepts optional websocket parameter for safe comparison.
        """
        # If a specific websocket was passed, only disconnect if it's the registered socket
        if user_id in self.active_connections:
            current_ws = self.active_connections[user_id]
            if websocket is None or current_ws == websocket:
                del self.active_connections[user_id]
                logger.info(f"[ConnectionManager] User '{user_id}' disconnected")

        # Clear busy call status when disconnecting
        if user_id in self.busy_status:
            del self.busy_status[user_id]

    async def send_to(self, user_id: str, message: Dict[str, Any]) -> bool:
        """
        Send JSON payload to user if online.
        Returns True if sent successfully, or False silently if not connected or send fails (do not raise).
        """
        websocket = self.active_connections.get(user_id)
        if not websocket:
            return False

        try:
            payload = json.dumps(message)
            await websocket.send_text(payload)
            return True
        except Exception as e:
            logger.warning(f"[ConnectionManager] Failed to send message to user '{user_id}': {e}")
            # Clean up dead socket silently
            self.disconnect(user_id, websocket)
            return False

    # Alias for send_to for backwards/existing compatibility
    async def send_personal_message(self, message: Dict[str, Any], user_id: str) -> bool:
        return await self.send_to(user_id, message)

    def is_online(self, user_id: str) -> bool:
        """Return True if user is currently connected, False otherwise."""
        return user_id in self.active_connections

    def get_online_users(self) -> List[str]:
        """Return list of all currently connected user IDs."""
        return list(self.active_connections.keys())

    # -------------------------------------------------------------
    # Call Busy State Management
    # -------------------------------------------------------------

    def is_busy(self, user_id: str) -> bool:
        """Return True if user is currently in an active call."""
        return self.busy_status.get(user_id) is not None

    def get_busy_call_id(self, user_id: str) -> Optional[str]:
        """Return current call_id user is participating in, or None."""
        return self.busy_status.get(user_id)

    def set_busy(self, user_id: str, call_id: str) -> bool:
        """
        Mark user as busy with call_id.
        Returns False if user is already busy in another call, True otherwise.
        """
        current = self.busy_status.get(user_id)
        if current and current != call_id:
            return False
        self.busy_status[user_id] = call_id
        return True

    def clear_busy(self, user_id: str):
        """Mark user as free/available."""
        if user_id in self.busy_status:
            self.busy_status[user_id] = None

    def set_call_participants(self, caller_id: str, callee_id: str, call_id: str) -> bool:
        """
        Mark both participants busy for call_id if neither is busy.
        Returns True if both successfully marked busy, False if either is already busy.
        """
        if self.is_busy(caller_id) or self.is_busy(callee_id):
            return False

        self.set_busy(caller_id, call_id)
        self.set_busy(callee_id, call_id)
        return True

    async def try_initiate_call(
        self, caller_id: str, callee_id: str, call_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Atomically verify that caller and callee are online and not busy under asyncio.Lock.
        Prevents race condition where concurrent call_requests double-assign a user.
        Returns:
            (True, None) if call was successfully initiated and both users marked busy.
            (False, "busy") if caller or callee is already busy.
            (False, "offline") if callee is offline.
        """
        async with self.lock:
            if self.is_busy(caller_id):
                return False, "busy"
            if not self.is_online(callee_id):
                return False, "offline"
            if self.is_busy(callee_id):
                return False, "busy"

            self.set_busy(caller_id, call_id)
            self.set_busy(callee_id, call_id)
            return True, None


    def get_call_partner(self, user_id: str) -> Optional[str]:
        """Find the user ID of the other participant in the user's active call, or None."""
        call_id = self.get_busy_call_id(user_id)
        if not call_id:
            return None
        for uid, cid in self.busy_status.items():
            if uid != user_id and cid == call_id:
                return uid
        return None

    def end_call(self, call_id: str):
        """Clear busy state for all participants associated with call_id."""
        for uid, cid in list(self.busy_status.items()):
            if cid == call_id:
                self.busy_status[uid] = None

    async def broadcast(self, message: Dict[str, Any], exclude_user: Optional[str] = None):
        """Broadcast a JSON message to all online users except optional exclude_user."""
        for uid, ws in list(self.active_connections.items()):
            if uid == exclude_user:
                continue
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                self.disconnect(uid, ws)


# Singleton instance shared across the application
manager = ConnectionManager()

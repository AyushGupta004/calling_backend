"""
Minimal WebSocket signaling server for 1-to-1 WebRTC audio calls.

Responsibilities (and ONLY these):
  - Let two clients join the same "room" via /ws/{room_id}
  - Relay "offer", "answer", and "ice-candidate" JSON messages between them
  - Clean up rooms/connections on disconnect

No auth, no persistence, no database. Pure in-memory relay.
"""

import json
import logging
import os
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("signaling")

app = FastAPI(title="Audio Call Signaling Server")

# room_id -> list of connected WebSocket clients (max 2)
rooms: Dict[str, List[WebSocket]] = {}

MAX_CLIENTS_PER_ROOM = 2
VALID_MESSAGE_TYPES = {"offer", "answer", "ice-candidate"}


@app.get("/")
async def health_check():
    """Simple health check so Render's health probe (and you) can verify it's alive."""
    return {"status": "ok", "active_rooms": len(rooms)}


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    room_id = room_id.strip()
    if not room_id:
        await websocket.close(code=4000, reason="room_id required")
        return

    existing = rooms.get(room_id, [])

    if len(existing) >= MAX_CLIENTS_PER_ROOM:
        # Room already has 2 peers — reject politely.
        await websocket.accept()
        await websocket.send_text(json.dumps({"type": "room-full"}))
        await websocket.close(code=4001, reason="room full")
        logger.info(f"[room={room_id}] rejected extra client — room full")
        return

    await websocket.accept()
    rooms.setdefault(room_id, []).append(websocket)
    logger.info(f"[room={room_id}] client connected ({len(rooms[room_id])}/2)")

    # Let the newly joined client know if a peer is already waiting.
    peer_already_present = len(rooms[room_id]) == 2
    await websocket.send_text(json.dumps({
        "type": "joined",
        "peer_present": peer_already_present
    }))

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[room={room_id}] dropped non-JSON message")
                continue

            msg_type = data.get("type")
            if msg_type not in VALID_MESSAGE_TYPES:
                logger.warning(f"[room={room_id}] dropped unknown type: {msg_type}")
                continue

            # Relay to the OTHER client in the room only.
            peers = rooms.get(room_id, [])
            for peer in peers:
                if peer is not websocket:
                    await peer.send_text(raw)

    except WebSocketDisconnect:
        logger.info(f"[room={room_id}] client disconnected")
    except Exception as e:
        logger.error(f"[room={room_id}] unexpected error: {e}")
    finally:
        # Cleanup: remove this client from the room.
        peers = rooms.get(room_id, [])
        if websocket in peers:
            peers.remove(websocket)

        # Notify the remaining peer, if any.
        for peer in peers:
            try:
                await peer.send_text(json.dumps({"type": "peer-disconnected"}))
            except Exception:
                pass

        # Delete the room entirely if empty.
        if not peers:
            rooms.pop(room_id, None)
            logger.info(f"[room={room_id}] room closed (empty)")
        else:
            rooms[room_id] = peers


if __name__ == "__main__":
    import uvicorn
    # Render injects the port to bind to via the PORT env var.
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
import asyncio
import json
import urllib.request
import urllib.error
import websockets
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import init_db, SessionLocal
from app.models import User, Contact

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000"

def run_http_request(endpoint: str, method: str = "GET", data: dict = None):
    url = f"{BASE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            resp_body = resp.read().decode("utf-8")
            return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        return e.code, json.loads(err_body) if err_body else {}


async def test_websocket_signaling(user_a_id: str, user_b_id: str):
    print("\n--- Testing WebSocket Signaling ---")
    async with websockets.connect(f"{WS_URL}/ws/{user_a_id}") as ws_a, \
               websockets.connect(f"{WS_URL}/ws/{user_b_id}") as ws_b:

        # 1. Read connected greeting for both
        conn_a = json.loads(await ws_a.recv())
        assert conn_a["type"] == "connected", f"Expected connected, got {conn_a}"
        print(f"User A ({user_a_id}) received connection acknowledgment")

        # User A may receive 'user-online' notification when User B connects
        conn_b = json.loads(await ws_b.recv())
        assert conn_b["type"] == "connected", f"Expected connected, got {conn_b}"
        print(f"User B ({user_b_id}) received connection acknowledgment")

        # Flush any user-online broadcast received by A
        try:
            online_broadcast = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=1.0))
            print(f"User A received presence broadcast: {online_broadcast.get('type')}")
        except asyncio.TimeoutError:
            pass

        # 2. User A sends call-request to User B
        call_req = {
            "type": "call-request",
            "target_user_id": user_b_id,
            "room_id": "test-call-room-123",
        }
        await ws_a.send(json.dumps(call_req))
        print("User A sent call-request to User B")

        # 3. User B should receive incoming-call
        incoming = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert incoming["type"] == "incoming-call", f"Expected incoming-call, got {incoming}"
        assert incoming["caller_id"] == user_a_id
        print("User B received incoming-call successfully")

        # 4. User B accepts call
        accept_msg = {
            "type": "call-accept",
            "target_user_id": user_a_id,
            "room_id": "test-call-room-123",
        }
        await ws_b.send(json.dumps(accept_msg))
        print("User B sent call-accept")

        # 5. User A receives call-accepted
        accepted = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert accepted["type"] == "call-accepted", f"Expected call-accepted, got {accepted}"
        print("User A received call-accepted successfully")

        # 6. WebRTC SDP Offer / Answer exchange
        offer_msg = {
            "type": "offer",
            "target_user_id": user_b_id,
            "sdp": {"type": "offer", "sdp": "v=0...mock-sdp-offer"},
        }
        await ws_a.send(json.dumps(offer_msg))
        recv_offer = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert recv_offer["type"] == "offer" and recv_offer["sdp"]["sdp"] == "v=0...mock-sdp-offer"
        print("WebRTC SDP Offer successfully routed from A to B")

        answer_msg = {
            "type": "answer",
            "target_user_id": user_a_id,
            "sdp": {"type": "answer", "sdp": "v=0...mock-sdp-answer"},
        }
        await ws_b.send(json.dumps(answer_msg))
        recv_answer = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert recv_answer["type"] == "answer" and recv_answer["sdp"]["sdp"] == "v=0...mock-sdp-answer"
        print("WebRTC SDP Answer successfully routed from B to A")

        # 7. ICE Candidate exchange
        ice_msg = {
            "type": "ice-candidate",
            "target_user_id": user_b_id,
            "candidate": {"candidate": "candidate:1 1 UDP ..."},
        }
        await ws_a.send(json.dumps(ice_msg))
        recv_ice = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert recv_ice["type"] == "ice-candidate"
        print("ICE Candidate successfully routed from A to B")

        # 8. Call termination (hang-up)
        hangup_msg = {"type": "hang-up", "target_user_id": user_b_id}
        await ws_a.send(json.dumps(hangup_msg))
        recv_ended = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert recv_ended["type"] == "call-ended"
        print("Hangup / call-ended successfully routed from A to B")

    print("WebSocket signaling verification passed!")


def main():
    print("Initializing Database...")
    init_db()
    print("Database tables initialized.")

    # 1. Health check
    status_code, health = run_http_request("/health")
    assert status_code == 200 and health.get("status") == "ok", f"Health check failed: {health}"
    print("[PASS] GET /health returned 200 OK")

    # 2. Register Users
    user_a_username = "alice_test"
    user_b_username = "bob_test"

    status_code, user_a = run_http_request("/users", "POST", {"name": "Alice Tester", "username": user_a_username})
    if status_code in (400, 409): # Already exists from previous run, look up via search
        status_code, search_res = run_http_request(f"/users/search?q={user_a_username}")
        user_a = search_res[0]
    assert "id" in user_a, f"Failed to get or create Alice: {user_a}"
    print(f"[PASS] User Alice verified: ID={user_a['id']}, username={user_a['username']}")

    status_code, user_b = run_http_request("/users", "POST", {"name": "Bob Tester", "username": user_b_username})
    if status_code in (400, 409):
        status_code, search_res = run_http_request(f"/users/search?q={user_b_username}")
        user_b = search_res[0]
    assert "id" in user_b, f"Failed to get or create Bob: {user_b}"
    print(f"[PASS] User Bob verified: ID={user_b['id']}, username={user_b['username']}")

    # 3. Add Contact
    status_code, contact = run_http_request("/contacts", "POST", {"user_id": user_a["id"], "contact_id": user_b["id"]})
    if status_code == 201:
        print(f"[PASS] Added Bob as contact for Alice: Contact ID={contact['id']}")
    elif status_code == 409:
        print("[PASS] Contact already present (Unique constraint enforced with 409)")
    else:
        assert False, f"Unexpected response adding contact: {status_code} {contact}"

    # 4. Verify Duplicate Prevention
    status_code, dup_resp = run_http_request("/contacts", "POST", {"user_id": user_a["id"], "contact_id": user_b["id"]})
    assert status_code == 409, f"Expected 409 for duplicate contact, got {status_code}: {dup_resp}"
    print("[PASS] Duplicate contact add prevented with HTTP 409")

    # 5. Verify Self-Contact Prevention
    status_code, self_resp = run_http_request("/contacts", "POST", {"user_id": user_a["id"], "contact_id": user_a["id"]})
    assert status_code == 400, f"Expected 400 for self-contact, got {status_code}: {self_resp}"
    print("[PASS] Self-contact add prevented with HTTP 400")

    # 6. List contacts
    status_code, contacts_list = run_http_request(f"/contacts/{user_a['id']}")
    assert status_code == 200 and len(contacts_list) > 0, f"Failed to list contacts: {contacts_list}"
    print(f"[PASS] Retrieved Alice's contacts: count={len(contacts_list)}")

    # 7. Run WebSocket Signaling Tests
    asyncio.run(test_websocket_signaling(user_a["id"], user_b["id"]))

    print("\nALL VERIFICATION TESTS COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    main()

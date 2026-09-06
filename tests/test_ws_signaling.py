import asyncio
import json
import os
import sys
import uuid
import websockets

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

WS_URL = "ws://127.0.0.1:8000"


async def run_signaling_tests():
    uid_a = f"alice_{uuid.uuid4().hex[:6]}"
    uid_b = f"bob_{uuid.uuid4().hex[:6]}"
    uid_c = f"charlie_{uuid.uuid4().hex[:6]}"

    print("\n--- Test 1: Call request to offline user ---")
    async with websockets.connect(f"{WS_URL}/ws/{uid_a}") as ws_a:
        # A calls offline B
        await ws_a.send(json.dumps({"type": "call_request", "to_user_id": uid_b}))
        resp = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert resp["type"] == "call_failed" and resp["reason"] == "offline", f"Got: {resp}"
        print("[PASS] Call request to offline user returns {call_failed, reason: offline}")

        # Test malformed JSON does not crash connection
        await ws_a.send("INVALID_NOT_JSON{{{")
        await ws_a.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert pong["type"] == "pong"
        print("[PASS] Malformed JSON handled gracefully without crashing connection")

    print("\n--- Test 2: Successful Call Request, Busy Prevention & Call Accept ---")
    async with websockets.connect(f"{WS_URL}/ws/{uid_a}") as ws_a, \
               websockets.connect(f"{WS_URL}/ws/{uid_b}") as ws_b, \
               websockets.connect(f"{WS_URL}/ws/{uid_c}") as ws_c:

        # 1. Alice calls Bob
        await ws_a.send(json.dumps({"type": "call_request", "to_user_id": uid_b}))
        incoming = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert incoming["type"] == "incoming_call"
        assert incoming["from_user_id"] == uid_a
        call_id = incoming["call_id"]
        assert call_id is not None
        print(f"[PASS] Bob received incoming_call from Alice with call_id: {call_id}")

        # 2. Charlie tries to call Bob while Bob is busy
        await ws_c.send(json.dumps({"type": "call_request", "to_user_id": uid_b}))
        c_resp = json.loads(await asyncio.wait_for(ws_c.recv(), timeout=2.0))
        assert c_resp["type"] == "call_failed" and c_resp["reason"] == "busy"
        print("[PASS] Third user (Charlie) calling busy user (Bob) receives {call_failed, reason: busy}")

        # 3. Alice tries to call Charlie while already busy calling Bob
        await ws_a.send(json.dumps({"type": "call_request", "to_user_id": uid_c}))
        a_resp = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert a_resp["type"] == "call_failed" and a_resp["reason"] == "busy"
        print("[PASS] Busy caller (Alice) calling another user receives {call_failed, reason: busy}")

        # 4. Bob accepts call
        await ws_b.send(json.dumps({
            "type": "call_accepted",
            "call_id": call_id,
            "to_user_id": uid_a,
        }))
        accepted = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert accepted["type"] == "call_accepted" and accepted["call_id"] == call_id
        print("[PASS] Alice received call_accepted from Bob")

        # 5. Signaling: WebRTC offer with valid call_id relayed
        offer_payload = {
            "type": "offer",
            "call_id": call_id,
            "to_user_id": uid_b,
            "sdp": {"type": "offer", "sdp": "v=0-mock-sdp"},
        }
        await ws_a.send(json.dumps(offer_payload))
        recv_offer = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert recv_offer["type"] == "offer" and recv_offer["sdp"]["sdp"] == "v=0-mock-sdp"
        print("[PASS] Offer relayed unchanged to Bob")

        # 6. Signaling: WebRTC answer with valid call_id relayed
        answer_payload = {
            "type": "answer",
            "call_id": call_id,
            "to_user_id": uid_a,
            "sdp": {"type": "answer", "sdp": "v=0-mock-answer"},
        }
        await ws_b.send(json.dumps(answer_payload))
        recv_answer = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert recv_answer["type"] == "answer" and recv_answer["sdp"]["sdp"] == "v=0-mock-answer"
        print("[PASS] Answer relayed unchanged to Alice")

        # 7. Signaling: ICE candidate with valid call_id relayed
        ice_payload = {
            "type": "ice_candidate",
            "call_id": call_id,
            "to_user_id": uid_b,
            "candidate": "candidate:mock 1",
        }
        await ws_a.send(json.dumps(ice_payload))
        recv_ice = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert recv_ice["type"] == "ice_candidate"
        print("[PASS] ICE candidate relayed unchanged to Bob")

        # 8. Signaling with INVALID call_id must be ignored/rejected
        bogus_offer = {
            "type": "offer",
            "call_id": "bogus-call-id-9999",
            "to_user_id": uid_b,
            "sdp": "fake",
        }
        await ws_a.send(json.dumps(bogus_offer))
        # Bob should not receive any message
        try:
            unwanted = await asyncio.wait_for(ws_b.recv(), timeout=0.5)
            assert False, f"Bob unexpectedly received bogus message: {unwanted}"
        except asyncio.TimeoutError:
            print("[PASS] Signaling message with invalid call_id was properly ignored")

        # 9. End call
        await ws_a.send(json.dumps({
            "type": "call_ended",
            "call_id": call_id,
            "to_user_id": uid_b,
        }))
        ended = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert ended["type"] == "call_ended" and ended["call_id"] == call_id
        print("[PASS] Bob received call_ended from Alice")

    print("\n--- Test 3: Call Rejection Clears Busy State ---")
    async with websockets.connect(f"{WS_URL}/ws/{uid_a}") as ws_a, \
               websockets.connect(f"{WS_URL}/ws/{uid_b}") as ws_b:

        await ws_a.send(json.dumps({"type": "call_request", "to_user_id": uid_b}))
        incoming = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        call_id_2 = incoming["call_id"]

        # Bob rejects call
        await ws_b.send(json.dumps({
            "type": "call_rejected",
            "call_id": call_id_2,
            "to_user_id": uid_a,
        }))
        rejected = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert rejected["type"] == "call_rejected" and rejected["call_id"] == call_id_2
        print("[PASS] Alice received call_rejected from Bob")

        # Confirm users are no longer busy by initiating a new call immediately
        await ws_a.send(json.dumps({"type": "call_request", "to_user_id": uid_b}))
        incoming_again = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert incoming_again["type"] == "incoming_call"
        print("[PASS] Verified busy state cleared after call_rejected (new call succeeded)")

    print("\n--- Test 4: Disconnect during active call sends peer_disconnected ---")
    async with websockets.connect(f"{WS_URL}/ws/{uid_b}") as ws_b:
        async with websockets.connect(f"{WS_URL}/ws/{uid_a}") as ws_a:
            await ws_a.send(json.dumps({"type": "call_request", "to_user_id": uid_b}))
            inc = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
            cid = inc["call_id"]

            await ws_b.send(json.dumps({
                "type": "call_accepted",
                "call_id": cid,
                "to_user_id": uid_a,
            }))
            await asyncio.wait_for(ws_a.recv(), timeout=2.0)
            # Alice disconnects suddenly
            print("Alice abruptly disconnecting WebSocket...")

        # Bob should receive call_ended with reason peer_disconnected
        disc_msg = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=2.0))
        assert disc_msg["type"] == "call_ended", f"Expected call_ended, got {disc_msg}"
        assert disc_msg.get("reason") == "peer_disconnected"
        assert disc_msg.get("call_id") == cid
        print("[PASS] Bob received {call_ended, reason: peer_disconnected} upon Alice disconnect")

    print("\n=======================================================")
    print("ALL WS.PY SIGNALING REQUIREMENTS VERIFIED AND PASSED!")
    print("=======================================================")


if __name__ == "__main__":
    asyncio.run(run_signaling_tests())

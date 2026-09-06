import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.database import init_db
from app.main import app

client = TestClient(app)


def make_request(endpoint: str, method: str = "GET", data: dict = None, headers: dict = None):
    """
    Helper function mirroring tests/test_b2_routers.py request interface.
    Returns (status_code, parsed_json).
    """
    req_headers = headers or {}
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"

    if method.upper() == "GET":
        resp = client.get(endpoint, headers=req_headers)
    elif method.upper() == "POST":
        resp = client.post(endpoint, json=data, headers=req_headers)
    elif method.upper() == "DELETE":
        resp = client.delete(endpoint, headers=req_headers)
    else:
        resp = client.request(method, endpoint, json=data, headers=req_headers)

    resp_body = resp.json() if resp.content else None
    return resp.status_code, resp_body


def run_tests():
    print("--- 1. Initializing DB ---")
    init_db()

    # Create a registered test user
    suffix = str(uuid.uuid4())[:8]
    test_username = f"turnuser_{suffix}"
    status, user = make_request("/users", "POST", {"name": "TURN Tester", "username": test_username})
    assert status == 201, f"Expected 201 when creating user, got {status}: {user}"
    user_id = user["id"]
    print(f"[PASS] Created test user: {user['name']} ({user_id})")

    print("\n--- 2. Testing Unauthenticated Access Rejection ---")
    # Missing user_id query parameter and missing X-User-Id header
    status, unauth_resp = make_request("/turn-credentials", "GET")
    assert status == 401, f"Expected 401 Unauthorized for unauthenticated request, got {status}: {unauth_resp}"
    print(f"[PASS] Unauthenticated request correctly rejected with HTTP 401: {unauth_resp}")

    print("\n--- 3. Testing Non-Existent User Rejection ---")
    # Non-existent user_id
    fake_user_id = str(uuid.uuid4())
    status, not_found_resp = make_request(f"/turn-credentials?user_id={fake_user_id}", "GET")
    assert status == 404, f"Expected 404 Not Found for non-existent user, got {status}: {not_found_resp}"
    print(f"[PASS] Non-existent user correctly rejected with HTTP 404: {not_found_resp}")

    print("\n--- 4. Testing GET /turn-credentials with Registered User (Unconfigured -> STUN Only) ---")
    status, creds_1 = make_request(f"/turn-credentials?user_id={user_id}", "GET")
    assert status == 200, f"Expected 200 OK, got {status}: {creds_1}"
    assert "iceServers" in creds_1, "Missing 'iceServers' in response"
    assert "ttl" in creds_1 and isinstance(creds_1["ttl"], int), "Missing or invalid 'ttl' in response"
    assert "turnConfigured" in creds_1, "Missing 'turnConfigured' in response"
    assert creds_1["turnConfigured"] is False, (
        f"Expected turnConfigured=False when TURN_HOST is left at default placeholder, got {creds_1['turnConfigured']}"
    )
    print(f"[PASS] Correctly detected placeholder TURN server: turnConfigured={creds_1['turnConfigured']}")

    ice_servers = creds_1["iceServers"]
    assert len(ice_servers) == 1, f"Expected exactly 1 iceServer (STUN-only when unconfigured), got {len(ice_servers)}"

    # Check for STUN server entry
    stun_entry = next((s for s in ice_servers if (isinstance(s.get("urls"), str) and s["urls"].startswith("stun:")) or (isinstance(s.get("urls"), list) and any(u.startswith("stun:") for u in s["urls"]))), None)
    assert stun_entry is not None, f"STUN entry not found in iceServers: {ice_servers}"
    print(f"[PASS] Found valid STUN-only server entry: {stun_entry}")

    # Ensure NO fake/placeholder TURN entries exist in unconfigured response
    turn_entries = [s for s in ice_servers if isinstance(s.get("urls"), list) and any(u.startswith("turn:") or u.startswith("turns:") for u in s["urls"])]
    assert len(turn_entries) == 0, f"Unconfigured response must NEVER return fake TURN entries: {turn_entries}"
    print("[PASS] Verified zero fake/placeholder TURN entries returned when unconfigured")

    print("\n--- 5. Testing Header-Based Authentication (X-User-Id) ---")
    status, header_resp = make_request("/turn-credentials", "GET", headers={"X-User-Id": user_id})
    assert status == 200, f"Expected 200 with X-User-Id header, got {status}: {header_resp}"
    assert header_resp["turnConfigured"] is False
    print("[PASS] Header-based authentication (X-User-Id) successfully authenticated request")

    print("\n--- 6. Testing Alias Endpoint /api/turn-credentials ---")
    status, alias_resp = make_request(f"/api/turn-credentials?user_id={user_id}", "GET")
    assert status == 200, f"Expected 200 on /api/turn-credentials, got {status}: {alias_resp}"
    assert alias_resp["turnConfigured"] is False
    print("[PASS] Alias endpoint /api/turn-credentials returned identical structure")

    print("\n--- 7. Testing Custom TTL Query Parameter ---")
    status, custom_ttl_resp = make_request(f"/turn-credentials?user_id={user_id}&ttl=7200", "GET")
    assert status == 200 and custom_ttl_resp["ttl"] == 7200
    print(f"[PASS] Custom TTL (7200) respected: {custom_ttl_resp['ttl']}")

    print("\n--- 8. Testing Configured Real TURN Host (turnConfigured=True, STUN + TURN) ---")
    orig_turn_host = os.environ.get("TURN_HOST")
    try:
        os.environ["TURN_HOST"] = "turn.production-relay.com"
        status, configured_resp_1 = make_request(f"/turn-credentials?user_id={user_id}", "GET")
        assert status == 200, f"Expected 200 OK, got {status}: {configured_resp_1}"
        assert configured_resp_1["turnConfigured"] is True, (
            f"Expected turnConfigured=True for real TURN_HOST, got {configured_resp_1['turnConfigured']}"
        )
        assert len(configured_resp_1["iceServers"]) >= 2, f"Expected at least 2 iceServers (STUN+TURN), got {len(configured_resp_1['iceServers'])}"

        turn_entry = next(
            (s for s in configured_resp_1["iceServers"] if isinstance(s.get("urls"), list) and any("turn.production-relay.com" in u for u in s["urls"])),
            None
        )
        assert turn_entry is not None, f"Expected custom TURN host in iceServers URLs: {configured_resp_1['iceServers']}"
        assert "username" in turn_entry and turn_entry["username"], "TURN entry missing username"
        assert "credential" in turn_entry and turn_entry["credential"], "TURN entry missing credential"
        assert f":{user_id}" in turn_entry["username"], f"TURN username should contain ':{user_id}', got {turn_entry['username']}"
        print(f"[PASS] Real TURN host successfully reflected: turnConfigured=True, urls={turn_entry['urls']}")

        # Test time-limited dynamic credentials change across calls
        time.sleep(1.1)
        status, configured_resp_2 = make_request(f"/turn-credentials?user_id={user_id}", "GET")
        turn_entry_2 = next((s for s in configured_resp_2["iceServers"] if isinstance(s.get("urls"), list) and any(u.startswith("turn:") for u in s["urls"])), None)
        assert turn_entry_2 is not None
        assert turn_entry["username"] != turn_entry_2["username"], "Usernames should differ across calls"
        assert turn_entry["credential"] != turn_entry_2["credential"], "Credentials should differ across calls"
        print("[PASS] Verified dynamic credentials differ across separate calls in configured mode")

    finally:
        if orig_turn_host is not None:
            os.environ["TURN_HOST"] = orig_turn_host
        else:
            os.environ.pop("TURN_HOST", None)

    print("\n--- 9. Testing /turn-check Diagnostic Endpoint ---")
    status, check_unconfigured = make_request("/turn-check", "GET")
    assert status == 200, f"Expected 200 OK, got {status}: {check_unconfigured}"
    assert check_unconfigured["turn_configured"] is False
    assert check_unconfigured["status"] == "unconfigured"
    print(f"[PASS] /turn-check accurately reported unconfigured status: {check_unconfigured['status']}")

    print("\n--- 10. Testing /health Endpoint TURN Status ---")
    status, health_resp = make_request("/health", "GET")
    assert status == 200, f"Expected 200 OK, got {status}: {health_resp}"
    assert "turn_configured" in health_resp, f"Expected 'turn_configured' field in /health response, got {health_resp}"
    print(f"[PASS] /health reports turn_configured: {health_resp['turn_configured']}")

    print("\n==============================================")
    print("ALL TURN CREDENTIALS REQUIREMENTS VERIFIED AND PASSED!")
    print("==============================================")




def test_turn_credentials():
    """Pytest test case entrypoint."""
    run_tests()


if __name__ == "__main__":
    run_tests()

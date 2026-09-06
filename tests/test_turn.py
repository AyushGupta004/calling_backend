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

    print("\n--- 4. Testing GET /turn-credentials with Registered User ---")
    status, creds_1 = make_request(f"/turn-credentials?user_id={user_id}", "GET")
    assert status == 200, f"Expected 200 OK, got {status}: {creds_1}"
    assert "iceServers" in creds_1, "Missing 'iceServers' in response"
    assert "ttl" in creds_1 and isinstance(creds_1["ttl"], int), "Missing or invalid 'ttl' in response"

    ice_servers = creds_1["iceServers"]
    assert len(ice_servers) >= 2, f"Expected at least 2 iceServers (STUN + TURN), got {len(ice_servers)}"

    # Check for STUN server entry
    stun_entry = next((s for s in ice_servers if (isinstance(s.get("urls"), str) and s["urls"].startswith("stun:")) or (isinstance(s.get("urls"), list) and any(u.startswith("stun:") for u in s["urls"]))), None)
    assert stun_entry is not None, f"STUN entry not found in iceServers: {ice_servers}"
    print(f"[PASS] Found valid STUN server entry: {stun_entry}")

    # Check for TURN server entry
    turn_entry = next((s for s in ice_servers if (isinstance(s.get("urls"), list) and any(u.startswith("turn:") or u.startswith("turns:") for u in s["urls"]))), None)
    assert turn_entry is not None, f"TURN entry not found in iceServers: {ice_servers}"
    assert "username" in turn_entry and turn_entry["username"], "TURN entry missing username"
    assert "credential" in turn_entry and turn_entry["credential"], "TURN entry missing credential"
    assert f":{user_id}" in turn_entry["username"], f"TURN username should contain ':{user_id}', got {turn_entry['username']}"
    print(f"[PASS] Found valid TURN server entry: username={turn_entry['username']}, credential={turn_entry['credential']}")

    print("\n--- 5. Testing Time-Limited Dynamic Credentials (Differ Across Calls) ---")
    # Wait over 1 second to ensure timestamp ticks forward
    time.sleep(1.1)

    status, creds_2 = make_request(f"/turn-credentials?user_id={user_id}", "GET")
    assert status == 200, f"Expected 200 OK on second call, got {status}: {creds_2}"

    turn_entry_2 = next((s for s in creds_2["iceServers"] if (isinstance(s.get("urls"), list) and any(u.startswith("turn:") for u in s["urls"]))), None)
    assert turn_entry_2 is not None

    username_1 = turn_entry["username"]
    credential_1 = turn_entry["credential"]
    username_2 = turn_entry_2["username"]
    credential_2 = turn_entry_2["credential"]

    assert username_1 != username_2, f"Usernames should differ across calls! 1: {username_1}, 2: {username_2}"
    assert credential_1 != credential_2, f"Credentials should differ across calls! 1: {credential_1}, 2: {credential_2}"
    print(f"[PASS] Verified time-limited credentials differ across separate calls:")
    print(f"       Call 1 username:   {username_1}")
    print(f"       Call 2 username:   {username_2}")
    print(f"       Call 1 credential: {credential_1}")
    print(f"       Call 2 credential: {credential_2}")

    print("\n--- 6. Testing Header-Based Authentication (X-User-Id) ---")
    status, header_resp = make_request("/turn-credentials", "GET", headers={"X-User-Id": user_id})
    assert status == 200, f"Expected 200 with X-User-Id header, got {status}: {header_resp}"
    assert len(header_resp["iceServers"]) >= 2
    print("[PASS] Header-based authentication (X-User-Id) successfully authenticated request")

    print("\n--- 7. Testing Alias Endpoint /api/turn-credentials ---")
    status, alias_resp = make_request(f"/api/turn-credentials?user_id={user_id}", "GET")
    assert status == 200, f"Expected 200 on /api/turn-credentials, got {status}: {alias_resp}"
    assert len(alias_resp["iceServers"]) >= 2
    print("[PASS] Alias endpoint /api/turn-credentials returned identical structure")

    print("\n--- 8. Testing Custom TTL Query Parameter ---")
    status, custom_ttl_resp = make_request(f"/turn-credentials?user_id={user_id}&ttl=7200", "GET")
    assert status == 200 and custom_ttl_resp["ttl"] == 7200
    print(f"[PASS] Custom TTL (7200) respected: {custom_ttl_resp['ttl']}")

    print("\n==============================================")
    print("ALL TURN CREDENTIALS REQUIREMENTS VERIFIED AND PASSED!")
    print("==============================================")


def test_turn_credentials():
    """Pytest test case entrypoint."""
    run_tests()


if __name__ == "__main__":
    run_tests()

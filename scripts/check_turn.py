#!/usr/bin/env python3
"""
TURN Server Diagnostic & Reachability Verification Tool
--------------------------------------------------------
Tests both:
1. The backend's /turn-credentials and /turn-check responses.
2. Direct UDP reachability to the STUN/TURN port (3478) via RFC 5389 Binding Request.

Usage:
  # Check local backend:
  python scripts/check_turn.py

  # Check deployed Render backend:
  python scripts/check_turn.py --backend-url https://calling-backend.onrender.com

  # Direct probe against a standalone TURN host:
  python scripts/check_turn.py --turn-host 203.0.113.10
"""

import argparse
import json
import socket
import sys
import time
import urllib.request
import urllib.error


def probe_stun_udp(host: str, port: int = 3478, timeout: float = 3.0) -> bool:
    """
    Send an RFC 5389 STUN Binding Request to (host, port) over UDP.
    Returns True if a valid STUN response is received.
    """
    print(f"\n[*] Probing UDP {host}:{port} with STUN Binding Request (timeout={timeout}s)...")
    # RFC 5389: Message Type: 0x0001 (Binding Request), Length: 0x0000, Magic Cookie: 0x2112A442
    req = (
        b"\x00\x01\x00\x00"
        b"\x21\x12\xa4\x42"
        b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c"
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    start_time = time.time()
    try:
        sock.sendto(req, (host, port))
        data, addr = sock.recvfrom(1024)
        elapsed_ms = (time.time() - start_time) * 1000
        if len(data) >= 20:
            msg_type = data[0:2]
            if msg_type in (b"\x01\x01", b"\x01\x11"):
                print(f"    [SUCCESS] Received STUN response from {addr} in {elapsed_ms:.1f}ms!")
                return True
        print(f"    [WARNING] Received unexpected payload of length {len(data)} from {addr}")
        return False
    except socket.timeout:
        print(f"    [FAIL] Timed out waiting for response from {host}:{port}.")
        print("           Firewall check: Ensure UDP port 3478 is open in your cloud security group/UFW.")
        return False
    except Exception as e:
        print(f"    [FAIL] Socket error: {e}")
        return False
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="Verify WebRTC TURN server configuration and reachability.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--turn-host", default=None, help="Directly test a TURN server IP/host")
    parser.add_argument("--port", type=int, default=3478, help="TURN port (default: 3478)")
    parser.add_argument("--user-id", default=None, help="User ID for /turn-credentials test")
    args = parser.parse_args()

    print("=" * 70)
    print("WebRTC TURN Configuration & Reachability Diagnostic")
    print("=" * 70)

    # 1. Direct host probe if specified
    if args.turn_host:
        success = probe_stun_udp(args.turn_host, args.port)
        if success:
            print(f"\n[OK] Port {args.port}/udp on '{args.turn_host}' is reachable and responding to STUN!")
        else:
            print(f"\n[FAIL] Port {args.port}/udp on '{args.turn_host}' did not respond.")
        return

    # 2. Query /turn-check on backend
    check_url = f"{args.backend_url.rstrip('/')}/turn-check"
    print(f"\n[*] Querying {check_url}...")
    try:
        req = urllib.request.Request(check_url, headers={"User-Agent": "turn-diag/1.0"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"    Status: {data.get('status')}")
            print(f"    TURN Configured: {data.get('turn_configured')}")
            print(f"    Current Host: {data.get('turn_host')}")
            if not data.get("turn_configured"):
                print("    [!] TURN_HOST is currently unconfigured or using the default placeholder.")
                print("        Backend will issue STUN-only credentials.")
            else:
                probe = data.get("udp_port_3478_probe", {})
                print(f"    UDP 3478 Probe: reachable={probe.get('reachable')} ({probe.get('note')})")
    except Exception as e:
        print(f"    [INFO] Could not query /turn-check: {e}")

    # 3. Create or find user and query /turn-credentials
    creds_url = f"{args.backend_url.rstrip('/')}/turn-credentials"
    user_id = args.user_id
    if not user_id:
        # Create a transient test user
        users_url = f"{args.backend_url.rstrip('/')}/users"
        test_uname = f"diag_{int(time.time())}"
        try:
            req = urllib.request.Request(
                users_url,
                data=json.dumps({"name": "Diag User", "username": test_uname}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                u = json.loads(resp.read().decode("utf-8"))
                user_id = u["id"]
        except Exception as e:
            print(f"    [INFO] Could not auto-create test user: {e}")

    if user_id:
        target_url = f"{creds_url}?user_id={user_id}"
        print(f"\n[*] Querying GET {target_url}...")
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "turn-diag/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                creds = json.loads(resp.read().decode("utf-8"))
                turn_configured = creds.get("turnConfigured", False)
                ice_servers = creds.get("iceServers", [])
                print(f"    turnConfigured: {turn_configured}")
                print(f"    Total iceServers returned: {len(ice_servers)}")

                turn_entry = next((s for s in ice_servers if isinstance(s.get("urls"), list) and any(u.startswith("turn:") for u in s["urls"])), None)
                if not turn_configured or not turn_entry:
                    print("\n[STUN-ONLY DETECTED]")
                    print("  Backend returned ONLY STUN servers because TURN_HOST is not yet configured.")
                    print("  WebRTC clients on different NATs/carrier data will NOT be able to relay calls.")
                else:
                    print("\n[WORKING TURN CREDENTIALS DETECTED]")
                    print(f"  URLs: {turn_entry['urls']}")
                    print(f"  Username: {turn_entry.get('username')}")
                    print(f"  Credential: {turn_entry.get('credential')}")

                    # Extract host and test UDP probe
                    first_url = turn_entry["urls"][0]
                    host_part = first_url.split("turn:")[1].split(":")[0]
                    probe_stun_udp(host_part, args.port)
        except Exception as e:
            print(f"    [FAIL] Failed to fetch credentials: {e}")

    print("\n" + "=" * 70)
    print("Verification Tools & Manual Testing")
    print("=" * 70)
    print("1. WebRTC Trickle ICE (Browser test):")
    print("   Open: https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/")
    print("   - Enter TURN URI: turn:<YOUR_TURN_HOST>:3478?transport=udp")
    print("   - Enter Username and Credential from /turn-credentials")
    print("   - Click 'Gather candidates' and verify candidate type 'relay' is gathered.")
    print("\n2. CLI coturn test (from any server/machine with coturn installed):")
    print('   turnutils_uclient -u "<USERNAME>" -w "<CREDENTIAL>" -e <YOUR_TURN_HOST> -p 3478 <YOUR_TURN_HOST>')
    print("=" * 70)


if __name__ == "__main__":
    main()

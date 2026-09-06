import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.connection_manager import ConnectionManager


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.cm = ConnectionManager()

    async def test_connect_and_is_online(self):
        mock_ws = AsyncMock()
        await self.cm.connect("user_1", mock_ws)

        mock_ws.accept.assert_awaited_once()
        self.assertTrue(self.cm.is_online("user_1"))
        self.assertFalse(self.cm.is_online("user_2"))
        self.assertIn("user_1", self.cm.active_connections)
        self.assertIsNone(self.cm.busy_status.get("user_1"))

    async def test_disconnect(self):
        mock_ws = AsyncMock()
        await self.cm.connect("user_1", mock_ws)
        self.cm.set_busy("user_1", "call_123")

        self.assertTrue(self.cm.is_online("user_1"))
        self.assertEqual(self.cm.get_busy_call_id("user_1"), "call_123")

        # Disconnect removes user and clears busy status
        self.cm.disconnect("user_1")
        self.assertFalse(self.cm.is_online("user_1"))
        self.assertNotIn("user_1", self.cm.active_connections)
        self.assertNotIn("user_1", self.cm.busy_status)

    async def test_send_to_online_user(self):
        mock_ws = AsyncMock()
        await self.cm.connect("user_1", mock_ws)

        success = await self.cm.send_to("user_1", {"type": "test", "data": 42})
        self.assertTrue(success)
        mock_ws.send_text.assert_awaited_once()
        payload = mock_ws.send_text.call_args[0][0]
        self.assertIn('"type": "test"', payload)

    async def test_send_to_offline_user_returns_false_silently(self):
        # Must return False silently, never raise
        success = await self.cm.send_to("non_existent_user", {"type": "ping"})
        self.assertFalse(success)

    async def test_send_to_broken_socket_returns_false_silently(self):
        mock_ws = AsyncMock()
        mock_ws.send_text.side_effect = RuntimeError("Broken pipe")
        await self.cm.connect("user_broken", mock_ws)

        # Should catch exception, clean up, and return False silently
        success = await self.cm.send_to("user_broken", {"type": "ping"})
        self.assertFalse(success)
        self.assertFalse(self.cm.is_online("user_broken"))

    def test_busy_call_enforcement(self):
        # 1. Initially users are not busy
        self.assertFalse(self.cm.is_busy("alice"))
        self.assertFalse(self.cm.is_busy("bob"))
        self.assertFalse(self.cm.is_busy("charlie"))

        # 2. Alice calls Bob
        call_id_1 = "call_alice_bob_001"
        success = self.cm.set_call_participants("alice", "bob", call_id_1)
        self.assertTrue(success)

        # Both are now marked busy with call_id_1
        self.assertTrue(self.cm.is_busy("alice"))
        self.assertTrue(self.cm.is_busy("bob"))
        self.assertEqual(self.cm.get_busy_call_id("alice"), call_id_1)
        self.assertEqual(self.cm.get_busy_call_id("bob"), call_id_1)

        # 3. Charlie tries to call Alice while Alice is in call_id_1
        call_id_2 = "call_charlie_alice_002"
        attempt = self.cm.set_call_participants("charlie", "alice", call_id_2)
        # Must reject: cannot participate in multiple active calls simultaneously
        self.assertFalse(attempt)
        self.assertFalse(self.cm.is_busy("charlie"))
        self.assertEqual(self.cm.get_busy_call_id("alice"), call_id_1)

        # 4. End call_id_1
        self.cm.end_call(call_id_1)
        self.assertFalse(self.cm.is_busy("alice"))
        self.assertFalse(self.cm.is_busy("bob"))

        # 5. Now Charlie can call Alice
        success_2 = self.cm.set_call_participants("charlie", "alice", call_id_2)
        self.assertTrue(success_2)
        self.assertTrue(self.cm.is_busy("charlie"))
        self.assertTrue(self.cm.is_busy("alice"))

    async def test_concurrent_call_requests_race_condition(self):
        """Verify that concurrent calls to the same user cannot double-assign under asyncio."""
        mock_a = AsyncMock()
        mock_b = AsyncMock()
        mock_c = AsyncMock()
        await self.cm.connect("alice", mock_a)
        await self.cm.connect("bob", mock_b)
        await self.cm.connect("charlie", mock_c)

        # Alice calls Bob, and Charlie calls Bob at the exact same moment
        res_a, res_c = await asyncio.gather(
            self.cm.try_initiate_call("alice", "bob", "call_a_b"),
            self.cm.try_initiate_call("charlie", "bob", "call_c_b"),
        )

        # Exactly ONE must succeed and the other must fail with 'busy'
        successes = [r for r in [res_a, res_c] if r[0] is True]
        failures = [r for r in [res_a, res_c] if r[0] is False]

        self.assertEqual(len(successes), 1, "Exactly one concurrent call must succeed")
        self.assertEqual(len(failures), 1, "The other concurrent call must fail")
        self.assertEqual(failures[0][1], "busy", "Failure reason must be 'busy'")

        # Bob must be assigned to the winning call, never double-assigned
        winning_call = self.cm.get_busy_call_id("bob")
        self.assertIn(winning_call, ["call_a_b", "call_c_b"])


if __name__ == "__main__":
    unittest.main()


import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.connection_manager import ConnectionManager


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = ConnectionManager()
        self.alice_socket = AsyncMock()
        self.bob_socket = AsyncMock()
        await self.manager.connect("alice", self.alice_socket)
        await self.manager.connect("bob", self.bob_socket)

    async def test_backend_creates_one_call_and_tracks_state(self):
        call, reason = await self.manager.create_call("alice", "bob")

        self.assertIsNone(reason)
        self.assertIsNotNone(call)
        self.assertEqual(call.state, "ringing")
        self.assertEqual(self.manager.get_call(call.call_id), call)
        self.assertEqual(self.manager.get_busy_call_id("alice"), call.call_id)
        self.assertEqual(self.manager.get_busy_call_id("bob"), call.call_id)

    async def test_busy_and_offline_are_rejected(self):
        call, _ = await self.manager.create_call("alice", "bob")
        self.assertIsNotNone(call)

        busy_call, busy_reason = await self.manager.create_call("alice", "bob")
        offline_call, offline_reason = await self.manager.create_call("charlie", "offline")

        self.assertIsNone(busy_call)
        self.assertEqual(busy_reason, "busy")
        self.assertIsNone(offline_call)
        self.assertEqual(offline_reason, "offline")

    async def test_end_call_clears_both_users(self):
        call, _ = await self.manager.create_call("alice", "bob")
        self.manager.end_call(call.call_id)

        self.assertIsNone(self.manager.get_call(call.call_id))
        self.assertFalse(self.manager.is_busy("alice"))
        self.assertFalse(self.manager.is_busy("bob"))

    async def test_old_socket_cannot_disconnect_new_socket(self):
        old_socket = self.alice_socket
        new_socket = AsyncMock()
        await self.manager.connect("alice", new_socket)

        self.manager.disconnect("alice", old_socket)

        self.assertTrue(self.manager.is_current_connection("alice", new_socket))
        self.assertTrue(self.manager.is_online("alice"))


if __name__ == "__main__":
    unittest.main()

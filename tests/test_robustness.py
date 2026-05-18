from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from llm_tg_bot.session import SessionManager
from llm_tg_bot.providers import ProviderSpec, ProviderAdapter, PreparedRequest, ProviderResponse, RequestContext
from llm_tg_bot.rendering import OutgoingMessage

class MockAdapter(ProviderAdapter):
    name = "mock"
    executable = "mock"
    def prepare_request(self, prompt, context, **kwargs):
        return PreparedRequest(command=("mock", prompt))
    def build_response(self, stdout, stderr, return_code, output_file):
        return ProviderResponse(text=stdout)

class RobustnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = ProviderSpec(adapter=MockAdapter())
        self.outputs = []
        self.cleaned_chats = []

        async def output_callback(chat_id, message):
            self.outputs.append((chat_id, message.text))

        def cleanup_callback(chat_id):
            self.cleaned_chats.append(chat_id)

        self.manager = SessionManager(
            providers={"mock": self.provider},
            idle_timeout_seconds=1,
            busy_timeout_seconds=2,
            output_callback=output_callback,
            cleanup_callback=cleanup_callback,
            max_queue_size=2
        )

    async def test_queue_limit(self):
        # First request starts immediately. We mock run_provider_request to stay pending.
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        
        async def slow_req(*args, **kwargs):
            await f1
            return None

        with patch("llm_tg_bot.session.run_provider_request", side_effect=slow_req):
            # First request starts immediately
            await self.manager.send_text(1, "p1", "mock")
            # Second and third are queued
            await self.manager.send_text(1, "p2", "mock")
            await self.manager.send_text(1, "p3", "mock")
            
            # Fourth should fail
            with self.assertRaises(RuntimeError) as cm:
                await self.manager.send_text(1, "p4", "mock")
            self.assertIn("Queue full", str(cm.exception))
            
            f1.set_result(None)

    async def test_chat_idle_cleanup_without_session(self):
        # Register activity for a chat
        self.manager.register_activity(123)
        self.assertIn(123, self.manager._chat_last_activity)
        
        # Wait for idle timeout
        await asyncio.sleep(1.1)
        await self.manager.stop_idle_sessions()
        
        # Check if cleanup was called
        self.assertIn(123, self.cleaned_chats)
        self.assertNotIn(123, self.manager._chat_last_activity)

    async def test_busy_session_timeout(self):
        # Mock a request that never finishes
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        
        async def slow_request(*args, **kwargs):
            try:
                await future
            except asyncio.CancelledError:
                pass
            return AsyncMock() # Return something that doesn't break result handling
            
        with patch("llm_tg_bot.session.run_provider_request", side_effect=slow_request):
            await self.manager.send_text(1, "slow", "mock")
            record = self.manager._records[1]
            self.assertTrue(record.is_busy)
            
            # Wait for busy timeout
            await asyncio.sleep(2.1)
            await self.manager.stop_idle_sessions()
            
            # Session should be stopped
            self.assertNotIn(1, self.manager._records)
            self.assertIn(1, self.cleaned_chats)
            self.assertIn("[session closed due to timeout]", self.outputs[-1][1])
            
            # Cleanup
            if not future.done():
                future.set_result(None)

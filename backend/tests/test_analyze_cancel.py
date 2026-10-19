"""分析接口取消行为测试；不调用真实大模型。"""

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.app.routers import analyze as analyze_router
from backend.app.schemas import AnalyzeRequest
from backend.app.services import llm as llm_service


class _DisconnectAfterFirstPoll:
    def __init__(self):
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls >= 2


class AnalyzeCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_cancels_async_llm_call(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def long_running_chat_json(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with (
            patch.object(
                analyze_router.resume_service,
                "resolve_profile_text",
                return_value="",
            ),
            patch.object(
                analyze_router.llm_service,
                "chat_json_async",
                side_effect=long_running_chat_json,
            ),
            patch.object(analyze_router, "_DISCONNECT_POLL_SECONDS", 0.01),
        ):
            route_task = asyncio.create_task(
                analyze_router.analyze(
                    AnalyzeRequest(transcript="需要分析的内容"),
                    _DisconnectAfterFirstPoll(),
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            with self.assertRaises(HTTPException) as raised:
                await asyncio.wait_for(route_task, timeout=1)

        self.assertEqual(499, raised.exception.status_code)
        self.assertTrue(cancelled.is_set())

    async def test_normal_analysis_returns_async_llm_result(self):
        expected = {"questions": [{"stem": "测试问题"}]}
        request = AnalyzeRequest(transcript="需要分析的内容")

        with (
            patch.object(
                analyze_router.resume_service,
                "resolve_profile_text",
                return_value="",
            ),
            patch.object(
                analyze_router.llm_service,
                "chat_json_async",
                return_value=expected,
            ) as chat_json_async,
        ):
            result = await analyze_router.analyze(request, _NeverDisconnect())

        self.assertEqual(expected, result)
        chat_json_async.assert_awaited_once()


class _NeverDisconnect:
    async def is_disconnected(self) -> bool:
        return False


class _FakeAsyncClient:
    def __init__(self):
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class LlmAsyncCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_llm_call_propagates_cancellation_and_closes_client(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        fake_client = _FakeAsyncClient()

        async def long_running_completion(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with (
            patch.object(
                llm_service,
                "_provider_config",
                return_value=("test-key", "http://example.invalid", "test-model"),
            ),
            patch.object(llm_service, "AsyncOpenAI", return_value=fake_client),
            patch.object(
                llm_service,
                "_acompletion",
                side_effect=long_running_completion,
            ),
        ):
            call_task = asyncio.create_task(
                llm_service.chat_json_async("system", "user")
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            call_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(call_task, timeout=1)

        self.assertTrue(cancelled.is_set())
        self.assertTrue(fake_client.closed)


if __name__ == "__main__":
    unittest.main()

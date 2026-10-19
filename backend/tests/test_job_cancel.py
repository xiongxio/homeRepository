"""转写任务取消测试；不加载真实 Whisper 模型、不访问网络。"""

import asyncio
import io
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.main import app
from backend.app.routers import jobs as jobs_router
from backend.app.schemas import TranscribeRequest
from backend.app.services import asr as asr_service
from backend.app.services import asr_runner, audio


class _BlockingAsrProcess:
    """模拟正在推理的子进程：transcribe 一直挂起，直到被取消。"""

    instances: list["_BlockingAsrProcess"] = []

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.started = False
        self.closed = False
        self.force_closed = False
        self.paths: list[Path] = []
        _BlockingAsrProcess.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def transcribe(self, path: Path) -> dict:
        self.paths.append(path)
        self.entered.set()
        await asyncio.Event().wait()

    async def close(self, force: bool = False) -> None:
        self.closed = True
        self.force_closed = self.force_closed or force


class _StubAsrProcess:
    """模拟正常返回的转写子进程。"""

    instances: list["_StubAsrProcess"] = []

    def __init__(self) -> None:
        self.closed = False
        self.force_closed = False
        self.paths: list[Path] = []
        _StubAsrProcess.instances.append(self)

    async def start(self) -> None:
        return None

    async def transcribe(self, path: Path) -> dict:
        self.paths.append(path)
        return {
            "text": f"文本{len(self.paths)}",
            "segments": [{"start": 0.0, "end": 1.5, "text": "你好"}],
        }

    async def close(self, force: bool = False) -> None:
        self.closed = True
        self.force_closed = self.force_closed or force


async def _fake_compress(src: Path, dst: Path) -> Path:
    dst.write_bytes(b"compressed")
    return dst


async def _fake_split(
    path: Path, max_bytes: int, workdir: Path, segment_seconds: int = 600
) -> list[tuple[Path, float]]:
    return [(path, 0.0)]


class JobCancelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._work_dir = jobs_router.WORK_DIR
        self._results_dir = jobs_router.RESULTS_DIR
        jobs_router.WORK_DIR = Path(self._tmp.name) / "work"
        jobs_router.RESULTS_DIR = Path(self._tmp.name) / "results"
        jobs_router.WORK_DIR.mkdir(parents=True, exist_ok=True)
        jobs_router.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._jobs = jobs_router._jobs
        self._runtime = jobs_router._runtime
        jobs_router._jobs = {}
        jobs_router._runtime = {}

        self._source = Path(self._tmp.name) / "input.mp4"
        self._source.write_bytes(b"fake-video")
        _BlockingAsrProcess.instances = []
        _StubAsrProcess.instances = []

    def tearDown(self) -> None:
        for runtime in self._runtime.values():
            task = runtime.get("task")
            if task is not None and not task.done():
                task.cancel()
        jobs_router._jobs = self._jobs
        jobs_router._runtime = self._runtime
        jobs_router.WORK_DIR = self._work_dir
        jobs_router.RESULTS_DIR = self._results_dir
        self._tmp.cleanup()

    def _patches(self, process_cls):
        return (
            patch.object(
                jobs_router,
                "get_file_record",
                return_value={"path": str(self._source), "size": 10, "filename": "input.mp4"},
            ),
            patch.object(jobs_router.audio, "compress_for_asr", side_effect=_fake_compress),
            patch.object(jobs_router.audio, "split_if_needed", side_effect=_fake_split),
            patch.object(jobs_router.audio, "probe_duration", return_value=12.0),
            patch.object(asr_runner, "AsrProcess", new=process_cls),
        )

    @contextmanager
    def _env(self, process_cls):
        patches = self._patches(process_cls)
        for item in patches:
            item.start()
        try:
            yield
        finally:
            for item in reversed(patches):
                item.stop()

    async def _wait_for_runner(self, job_id: str):
        for _ in range(400):
            runtime = jobs_router._runtime.get(job_id)
            if runtime is not None and runtime.get("runner") is not None:
                return runtime
            await asyncio.sleep(0.005)
        self.fail("转写子进程始终没有启动")

    async def test_cancel_running_job_kills_worker_and_writes_no_result(self):
        with self._env(_BlockingAsrProcess):
            created = await jobs_router.create_job(TranscribeRequest(file_id="file-1"))
            job_id = created.job_id
            runtime = await self._wait_for_runner(job_id)
            process = _BlockingAsrProcess.instances[0]
            await asyncio.wait_for(process.entered.wait(), timeout=5)

            response = await jobs_router.cancel_job(job_id)
            self.assertEqual("cancelled", response["status"])
            self.assertTrue(runtime["cancel_requested"])

            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(runtime["task"], timeout=5)

            self.assertEqual("cancelled", jobs_router._jobs[job_id]["status"])
            self.assertTrue(process.closed)
            self.assertTrue(process.force_closed)
            self.assertFalse((jobs_router.RESULTS_DIR / f"{job_id}.json").exists())
            self.assertNotIn(job_id, jobs_router._runtime)

    async def test_normal_transcription_still_returns_result(self):
        with self._env(_StubAsrProcess):
            created = await jobs_router.create_job(TranscribeRequest(file_id="file-1"))
            job_id = created.job_id
            # 桩函数返回很快，任务可能已经跑完，直接等任务本身即可。
            await asyncio.wait_for(jobs_router._runtime[job_id]["task"], timeout=5)

            job = jobs_router._jobs[job_id]
            self.assertEqual("done", job["status"])
            self.assertEqual(1.0, job["progress"])
            self.assertEqual("文本1", job["result"]["text"])
            self.assertEqual(12.0, job["result"]["duration"])
            self.assertEqual(1, job["result"]["segment_count"])
            self.assertEqual(
                [{"start": 0.0, "end": 1.5, "text": "你好"}],
                job["result"]["segments"],
            )
            saved = json.loads(
                (jobs_router.RESULTS_DIR / f"{job_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(job["result"]["text"], saved["text"])
            process = _StubAsrProcess.instances[0]
            self.assertTrue(process.closed)
            self.assertFalse(process.force_closed)
            self.assertEqual([], list((jobs_router.WORK_DIR / job_id).glob("*")))

    async def test_cancel_finished_job_is_idempotent(self):
        jobs_router._jobs["finished"] = {
            "job_id": "finished",
            "status": "done",
            "progress": 1.0,
            "result": {"text": "已完成"},
        }
        response = await jobs_router.cancel_job("finished")
        self.assertEqual("done", response["status"])
        self.assertEqual("已完成", jobs_router._jobs["finished"]["result"]["text"])

    async def test_task_callback_cleans_runtime_when_coroutine_never_enters(self):
        async def boom(job_id: str, file_id: str) -> None:
            raise RuntimeError("boom")

        with (
            patch.object(jobs_router, "get_file_record", return_value={"path": "unused"}),
            patch.object(jobs_router, "_run_transcribe", new=boom),
        ):
            created = await jobs_router.create_job(TranscribeRequest(file_id="file-1"))
            job_id = created.job_id
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(jobs_router._runtime[job_id]["task"], timeout=5)

        for _ in range(50):
            if job_id not in jobs_router._runtime:
                break
            await asyncio.sleep(0)
        self.assertNotIn(job_id, jobs_router._runtime)

    async def test_cancel_unknown_job_returns_404(self):
        with self.assertRaises(HTTPException) as raised:
            await jobs_router.cancel_job("missing")
        self.assertEqual(404, raised.exception.status_code)

    async def test_cancelled_job_state_is_json_serializable(self):
        """GET /api/jobs/{id} 直接返回 _jobs 字典，必须只含可序列化字段。"""
        with self._env(_BlockingAsrProcess):
            created = await jobs_router.create_job(TranscribeRequest(file_id="file-1"))
            job_id = created.job_id
            runtime = await self._wait_for_runner(job_id)
            await jobs_router.cancel_job(job_id)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(runtime["task"], timeout=5)
            self.assertEqual(
                jobs_router._jobs[job_id],
                json.loads(json.dumps(jobs_router._jobs[job_id])),
            )


class JobCancelApiTests(unittest.TestCase):
    """走真实 HTTP 路由的取消冒烟测试。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._work_dir = jobs_router.WORK_DIR
        self._results_dir = jobs_router.RESULTS_DIR
        jobs_router.WORK_DIR = Path(self._tmp.name) / "work"
        jobs_router.RESULTS_DIR = Path(self._tmp.name) / "results"
        jobs_router.WORK_DIR.mkdir(parents=True, exist_ok=True)
        jobs_router.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._jobs = jobs_router._jobs
        self._runtime = jobs_router._runtime
        jobs_router._jobs = {}
        jobs_router._runtime = {}
        self._source = Path(self._tmp.name) / "input.mp4"
        self._source.write_bytes(b"fake-video")
        _BlockingAsrProcess.instances = []
        self._patches = [
            patch.object(
                jobs_router,
                "get_file_record",
                return_value={"path": str(self._source), "size": 10, "filename": "input.mp4"},
            ),
            patch.object(jobs_router.audio, "compress_for_asr", side_effect=_fake_compress),
            patch.object(jobs_router.audio, "split_if_needed", side_effect=_fake_split),
            patch.object(jobs_router.audio, "probe_duration", return_value=12.0),
            patch.object(asr_runner, "AsrProcess", new=_BlockingAsrProcess),
            # 测试期间不碰真实数据目录：启动清理与目录准备都指向临时目录。
            patch.object(main_module, "_cleanup_stale", lambda: None),
            patch.object(main_module, "RESULTS_DIR", jobs_router.RESULTS_DIR),
            patch.object(main_module, "UPLOADS_DIR", Path(self._tmp.name) / "uploads"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        jobs_router._jobs = self._jobs
        jobs_router._runtime = self._runtime
        jobs_router.WORK_DIR = self._work_dir
        jobs_router.RESULTS_DIR = self._results_dir
        self._tmp.cleanup()

    def test_cancel_endpoint_stops_running_job(self):
        with TestClient(app) as client:
            created = client.post("/api/jobs", json={"file_id": "file-1"})
            self.assertEqual(200, created.status_code, created.text)
            job_id = created.json()["job_id"]

            for _ in range(400):
                state = client.get(f"/api/jobs/{job_id}").json()
                if state["status"] == "running":
                    break
                time.sleep(0.005)
            self.assertEqual("running", state["status"])

            cancelled = client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(200, cancelled.status_code, cancelled.text)
            self.assertEqual("cancelled", cancelled.json()["status"])

            again = client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(200, again.status_code)
            self.assertEqual("cancelled", again.json()["status"])
            self.assertEqual("cancelled", client.get(f"/api/jobs/{job_id}").json()["status"])
            self.assertEqual(404, client.post("/api/jobs/missing/cancel").status_code)

        process = _BlockingAsrProcess.instances[0]
        self.assertTrue(process.closed)
        self.assertTrue(process.force_closed)
        self.assertFalse((jobs_router.RESULTS_DIR / f"{job_id}.json").exists())


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    async def communicate(self):
        await asyncio.Event().wait()

    async def wait(self) -> int:
        self.returncode = -9
        return self.returncode


class AudioCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_kills_ffmpeg_when_task_cancelled(self):
        process = _HangingProcess()

        class _Shim:
            subprocess = asyncio.subprocess
            CancelledError = asyncio.CancelledError

            @staticmethod
            async def create_subprocess_exec(*args, **kwargs):
                return process

        with patch.object(audio, "asyncio", _Shim):
            task = asyncio.create_task(audio._run(["ffmpeg", "-i", "input.mp4"]))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        self.assertTrue(process.killed)


class WorkerProtocolTests(unittest.TestCase):
    def test_worker_reuses_one_process_for_all_segments(self):
        calls: list[Path] = []

        def fake_transcribe(path: Path) -> dict:
            calls.append(path)
            return {"text": f"文本-{path.name}", "segments": []}

        payload = (
            b'{"cmd": "transcribe", "path": "seg1.webm"}\n'
            b'{"cmd": "transcribe", "path": "seg2.webm"}\n'
            b'{"cmd": "stop"}\n'
            b'{"cmd": "transcribe", "path": "ignored.webm"}\n'
        )
        out = io.BytesIO()
        with patch.object(asr_service, "transcribe_file", side_effect=fake_transcribe):
            code = asr_runner.run_worker(stdin=io.BytesIO(payload), stdout=out)

        self.assertEqual(0, code)
        messages = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(2, len(messages))
        self.assertEqual(["seg1.webm", "seg2.webm"], [path.name for path in calls])
        self.assertTrue(all(message["ok"] for message in messages))
        self.assertEqual("文本-seg1.webm", messages[0]["data"]["text"])

    def test_worker_reports_failure_without_crashing(self):
        def boom(path: Path) -> dict:
            raise asr_service.AsrError("模型加载失败")

        payload = (
            b'{"cmd": "transcribe", "path": "seg1.webm"}\n'
            b'{"cmd": "transcribe", "path": "seg2.webm"}\n'
            b'{"cmd": "stop"}\n'
        )
        out = io.BytesIO()
        with patch.object(asr_service, "transcribe_file", side_effect=boom):
            asr_runner.run_worker(stdin=io.BytesIO(payload), stdout=out)

        messages = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(2, len(messages))
        for message in messages:
            self.assertFalse(message["ok"])
            self.assertIn("模型加载失败", message["error"])


class AsrProcessClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_round_trip_and_force_close(self):
        class _Stdin:
            def __init__(self) -> None:
                self.data = bytearray()

            def write(self, payload: bytes) -> None:
                self.data += payload

            async def drain(self) -> None:
                return None

            def is_closing(self) -> bool:
                return False

            def close(self) -> None:
                return None

        class _Stdout:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = lines

            async def readline(self) -> bytes:
                return self.lines.pop(0) if self.lines else b""

        class _Process:
            def __init__(self) -> None:
                self.returncode: int | None = None
                self.stdin = _Stdin()
                self.stdout = _Stdout([b'{"ok": true, "data": {"text": "\\u4f60\\u597d"}}\n'])
                self.stderr = None
                self.killed = False

            def kill(self) -> None:
                self.killed = True

            async def wait(self) -> int:
                self.returncode = -9 if self.killed else 0
                return self.returncode

        process = _Process()

        async def fake_create(*args, **kwargs):
            return process

        runner = asr_runner.AsrProcess()
        with patch.object(asyncio, "create_subprocess_exec", new=fake_create):
            await runner.start()
            data = await runner.transcribe(Path("seg.webm"))

        self.assertEqual("你好", data["text"])
        self.assertIn(b'"cmd": "transcribe"', bytes(process.stdin.data))
        self.assertIn(b"seg.webm", bytes(process.stdin.data))

        await runner.close(force=True)
        self.assertTrue(process.killed)
        self.assertFalse(runner.running)

    async def test_transcribe_reports_worker_exit(self):
        class _Stdin:
            def write(self, payload: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

            def is_closing(self) -> bool:
                return False

            def close(self) -> None:
                return None

        class _Stdout:
            async def readline(self) -> bytes:
                return b""

        class _Process:
            def __init__(self) -> None:
                self.returncode = 1
                self.stdin = _Stdin()
                self.stdout = _Stdout()
                self.stderr = None

            def kill(self) -> None:
                return None

            async def wait(self) -> int:
                return self.returncode

        process = _Process()

        async def fake_create(*args, **kwargs):
            return process

        runner = asr_runner.AsrProcess()
        with patch.object(asyncio, "create_subprocess_exec", new=fake_create):
            await runner.start()
            with self.assertRaises(asr_service.AsrError):
                await runner.transcribe(Path("seg.webm"))
        await runner.close()


class WorkerProcessSmokeTests(unittest.IsolatedAsyncioTestCase):
    """真实拉起一次子进程，验证 -m 启动方式、PYTHONPATH 和 stop 协议。"""

    async def test_real_worker_starts_and_stops_cleanly(self):
        runner = asr_runner.AsrProcess()
        await runner.start()
        process = runner._proc
        self.assertIsNotNone(process)
        try:
            process.stdin.write(b'{"cmd": "stop"}\n')
            await process.stdin.drain()
            code = await asyncio.wait_for(process.wait(), timeout=60)
            self.assertEqual(0, code, runner._failure_reason())
        finally:
            await runner.close(force=True)

    async def test_real_worker_is_terminated_by_force_close(self):
        """取消走的就是这条路径：真实子进程必须被真正终止，而不是继续跑。"""
        runner = asr_runner.AsrProcess()
        await runner.start()
        process = runner._proc
        self.assertIsNotNone(process)
        await asyncio.sleep(0.5)
        self.assertIsNone(process.returncode)

        await asyncio.wait_for(runner.close(force=True), timeout=30)

        self.assertIsNotNone(process.returncode)
        self.assertNotEqual(0, process.returncode)


if __name__ == "__main__":
    unittest.main()

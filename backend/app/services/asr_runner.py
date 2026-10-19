"""把 faster-whisper 转写放进独立子进程，让「取消转写」真的能停下来。

同步推理一旦开始就无法被 asyncio 取消，只能终止整个进程；因此每个转写任务
启动一个常驻 worker，任务内的多个分片复用同一个进程（模型只加载一次），
取消时直接 terminate/kill 该进程。

协议：父进程往 worker 的 stdin 写一行 JSON，worker 处理完往 stdout 回一行
JSON。库自身打印的内容会被引到 stderr，避免污染协议。
"""

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from . import asr
from .asr import AsrError

_WORKER_PATH = Path(__file__).resolve()
# 以 -m 方式启动时，包名取决于调用方的导入路径（app.services.x 或
# backend.app.services.x），按当前模块的 __package__ 推导，两种都能用。
_PACKAGE = __package__ or ""
_MODULE_NAME = f"{_PACKAGE}.asr_runner" if _PACKAGE else _WORKER_PATH.stem
_IMPORT_ROOT = _WORKER_PATH.parents[len(_PACKAGE.split(".")) if _PACKAGE else 3]

# 长音频的转写结果可能包含几千个分片，默认 64KiB 的管道缓冲装不下。
_STREAM_LIMIT = 32 * 1024 * 1024
_STOP_TIMEOUT_SECONDS = 1.0
_STDERR_TAIL_LINES = 20


def _child_env() -> dict[str, str]:
    """让子进程无论如何都能 import 到本模块，同时保持 .env 的解析目录不变。"""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [str(_IMPORT_ROOT)] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _binary(stream):
    """文本流取其 binary buffer；已经是二进制流（单测注入）则原样返回。"""
    return getattr(stream, "buffer", stream)


def run_worker(stdin=None, stdout=None) -> int:
    """worker 主循环；stdin/stdout 可注入，方便单测。"""
    reader = _binary(stdin if stdin is not None else sys.stdin)
    writer = _binary(stdout if stdout is not None else sys.stdout)

    for raw in reader:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        command = message.get("cmd")
        if command == "stop":
            break
        if command != "transcribe":
            continue
        try:
            data = asr.transcribe_file(Path(message["path"]))
            payload: dict = {"ok": True, "data": data}
        except Exception as exc:  # noqa: BLE001 - 任何失败都要回报给父进程
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        writer.flush()
    return 0


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


class AsrProcess:
    """父进程侧的转写子进程句柄：一个任务一个进程，可被强制终止。"""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: list[str] = []
        self._stderr_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            _MODULE_NAME,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_env(),
            limit=_STREAM_LIMIT,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            try:
                line = await proc.stderr.readline()
            except (ValueError, asyncio.LimitOverrunError):
                break
            if not line:
                return
            self._stderr_tail.append(line.decode("utf-8", errors="replace").rstrip())
            del self._stderr_tail[:-_STDERR_TAIL_LINES]

    def _failure_reason(self) -> str:
        code = self._proc.returncode if self._proc is not None else None
        head = "转写进程已退出" if code is None else f"转写进程异常退出（exit code {code}）"
        tail = "\n".join(self._stderr_tail[-8:]).strip()
        return f"{head}：\n{tail}" if tail else f"{head}。"

    async def transcribe(self, path: Path) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise AsrError("转写进程未启动。")
        if proc.returncode is not None:
            raise AsrError(self._failure_reason())
        payload = json.dumps(
            {"cmd": "transcribe", "path": str(path)}, ensure_ascii=False
        ).encode("utf-8")
        try:
            proc.stdin.write(payload + b"\n")
            await proc.stdin.drain()
            raw = await proc.stdout.readline()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AsrError(self._failure_reason()) from exc
        except ValueError as exc:  # 单行结果超过管道上限
            raise AsrError(f"转写结果过大，无法读取：{exc}") from exc
        if not raw:
            raise AsrError(self._failure_reason())
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AsrError(f"转写进程返回了无法解析的数据：{raw[:200]!r}") from exc
        if not message.get("ok"):
            raise AsrError(str(message.get("error") or "转写失败。"))
        return message.get("data") or {}

    async def close(self, force: bool = False) -> None:
        """force=True 直接终止；否则先请 worker 自己退出，超时才强杀。"""
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            if force:
                _kill(proc)
            else:
                with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
                    if proc.stdin is not None and not proc.stdin.is_closing():
                        proc.stdin.write(b'{"cmd": "stop"}\n')
                        await proc.stdin.drain()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_STOP_TIMEOUT_SECONDS)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    _kill(proc)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await proc.wait()
        if proc is not None and proc.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                proc.stdin.close()
        task, self._stderr_task = self._stderr_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


if __name__ == "__main__":
    # 协议输出必须用原始 stdout；之后库里的 print 一律走 stderr。
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    raise SystemExit(run_worker(stdin=sys.stdin, stdout=protocol_out))

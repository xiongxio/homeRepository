import asyncio
import contextlib
import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..schemas import JobCreateResponse, TranscribeRequest
from ..services import asr, asr_runner, audio
from .uploads import get_file_record

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

RESULTS_DIR = settings.data_dir / "results"
WORK_DIR = settings.data_dir / "work"

_jobs: dict[str, dict] = {}
# 任务句柄（asyncio.Task、转写子进程）与对外返回的状态分开存，
# 否则 GET /api/jobs/{id} 会序列化不出去。
_runtime: dict[str, dict] = {}


def _set(job_id: str, **kw) -> None:
    _jobs[job_id].update(kw)


_TERMINAL_STATUSES = ("done", "error", "cancelled")


@router.post("", response_model=JobCreateResponse)
async def create_job(req: TranscribeRequest):
    get_file_record(req.file_id)
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "job_id": job_id,
        "file_id": req.file_id,
        "status": "queued",
        "stage": "排队中",
        "progress": 0.0,
        "created": time.time(),
    }
    task = asyncio.create_task(_run_transcribe(job_id, req.file_id))
    _runtime[job_id] = {
        "task": task,
        "runner": None,
        "cancel_requested": False,
    }
    # 任务如果在真正开始前就被取消，协程不会进入 finally；这里兜底清理句柄。
    task.add_done_callback(lambda _task, jid=job_id: _runtime.pop(jid, None))
    return JobCreateResponse(job_id=job_id)


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job:
        return job
    result_path = RESULTS_DIR / f"{job_id}.json"
    if result_path.exists():
        return {
            "job_id": job_id,
            "status": "done",
            "progress": 1.0,
            "result": json.loads(result_path.read_text(encoding="utf-8")),
        }
    raise HTTPException(404, "任务不存在")


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """取消转写：终止推理子进程，并停掉还在跑的 ffmpeg。已是终态则幂等返回。"""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.get("status") in _TERMINAL_STATUSES:
        return job
    runtime = _runtime.get(job_id)
    if runtime is not None:
        runtime["cancel_requested"] = True
        task = runtime.get("task")
        if task is not None and not task.done():
            task.cancel()
    _set(job_id, status="cancelled", stage="已取消")
    return _jobs[job_id]


async def _run_transcribe(job_id: str, file_id: str) -> None:
    workdir = WORK_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    runner: asr_runner.AsrProcess | None = None
    cancel_requested = False
    try:
        record = get_file_record(file_id)
        src = Path(record["path"])

        _set(job_id, status="running", stage="音频压缩", progress=0.05)
        compressed = await audio.compress_for_asr(src, workdir / "compressed.webm")

        _set(job_id, stage="分片处理", progress=0.15)
        segments = await audio.split_if_needed(compressed, settings.whisper_max_bytes, workdir / "segments")

        runner = asr_runner.AsrProcess()
        await runner.start()
        if _runtime.get(job_id) is not None:
            _runtime[job_id]["runner"] = runner

        texts: list[str] = []
        all_segments: list[dict] = []
        for i, (seg_path, offset) in enumerate(segments):
            _set(
                job_id,
                stage=f"语音转写 {i + 1}/{len(segments)}",
                progress=0.2 + 0.7 * (i / max(len(segments), 1)),
            )
            data = await runner.transcribe(seg_path)
            text = (data.get("text") or "").strip()
            if text:
                texts.append(text)
            for seg in data.get("segments") or []:
                all_segments.append(
                    {
                        "start": round(offset + float(seg.get("start", 0)), 2),
                        "end": round(offset + float(seg.get("end", 0)), 2),
                        "text": (seg.get("text") or "").strip(),
                    }
                )
            _set(job_id, progress=0.2 + 0.7 * ((i + 1) / max(len(segments), 1)))

        _set(job_id, stage="整理结果", progress=0.95)
        duration = await audio.probe_duration(compressed)
        result = {
            "text": asr.join_transcripts(texts),
            "segments": all_segments,
            "duration": round(duration, 2),
            "original_size": record["size"],
            "compressed_size": compressed.stat().st_size,
            "segment_count": len(segments),
        }
        (RESULTS_DIR / f"{job_id}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        _set(job_id, status="done", stage="完成", progress=1.0, result=result)
    except asyncio.CancelledError:
        cancel_requested = True
        _set(job_id, status="cancelled", stage="已取消")
        raise
    except Exception as exc:
        _set(job_id, status="error", error=str(exc))
    finally:
        if runner is not None:
            # 同步推理不会理 stop，取消时必须强杀子进程。
            with contextlib.suppress(BaseException):
                await runner.close(force=cancel_requested)
        _runtime.pop(job_id, None)
        shutil.rmtree(workdir, ignore_errors=True)

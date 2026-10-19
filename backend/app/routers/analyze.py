import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..services import llm as llm_service
from ..services import resume as resume_service
from .. import prompts
from ..schemas import AnalyzeRequest

router = APIRouter(prefix="/api/analyze", tags=["analyze"])

_DISCONNECT_POLL_SECONDS = 0.25


async def _wait_for_analysis_or_disconnect(request: Request, task: asyncio.Task[dict]) -> dict:
    """等待分析完成；客户端断开时取消任务，避免后台继续消耗 LLM。"""
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_DISCONNECT_POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                raise HTTPException(499, "客户端已取消分析")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@router.post("")
async def analyze(req: AnalyzeRequest, request: Request):
    if not req.transcript.strip():
        raise HTTPException(400, "转写文本不能为空")
    resume_profile = resume_service.resolve_profile_text(req.resume_id)
    user = prompts.build_analysis_user(
        req.jd, req.resume, req.transcript, req.knowledge, resume_profile
    )
    try:
        analysis_task = asyncio.create_task(
            llm_service.chat_json_async(prompts.ANALYSIS_SYSTEM, user)
        )
        analysis = await _wait_for_analysis_or_disconnect(request, analysis_task)
    except llm_service.LlmError as exc:
        raise HTTPException(502, str(exc))
    if not isinstance(analysis.get("questions"), list):
        raise HTTPException(502, "LLM 返回结构异常（缺少 questions），请重试。")
    return analysis

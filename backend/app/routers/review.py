import asyncio

from fastapi import APIRouter, HTTPException, Query

from ..schemas import ReviewErrorCauseRequest, ReviewRedoRequest
from ..services import review as review_service


router = APIRouter(prefix="/api/quiz/review", tags=["quiz-review"])


@router.get("/dashboard")
async def dashboard():
    return review_service.get_dashboard()


@router.get("/mistakes")
async def mistakes():
    return {"records": review_service.list_records()}


@router.get("/knowledge-map")
async def knowledge_map():
    return review_service.get_knowledge_map()


@router.post("/sync-retry")
async def sync_retry():
    return await asyncio.to_thread(review_service.retry_pending_syncs)


@router.post("/tasks/{topic_id}/practice")
async def practice(
    topic_id: str,
    include_today: bool = Query(default=False),
    include_active: bool = Query(default=False),
):
    try:
        paper = await asyncio.to_thread(
            review_service.generate_practice_paper,
            topic_id,
            5,
            include_today,
            include_active,
        )
    except review_service.ReviewError as exc:
        raise HTTPException(422, str(exc))
    from ..services import quiz as quiz_service

    return quiz_service.get_paper(paper["paper_id"])


@router.post("/tasks/{topic_id}/redo")
async def redo(
    topic_id: str,
    req: ReviewRedoRequest,
):
    try:
        paper = await asyncio.to_thread(
            review_service.generate_redo_paper,
            topic_id,
            req.review_ids,
        )
    except review_service.ReviewError as exc:
        raise HTTPException(422, str(exc))
    from ..services import quiz as quiz_service

    return quiz_service.get_paper(paper["paper_id"])


@router.put("/{review_id}/error-causes")
async def update_error_cause(review_id: str, req: ReviewErrorCauseRequest):
    try:
        record = await asyncio.to_thread(
            review_service.update_error_cause, review_id, req.cause
        )
    except review_service.ReviewError as exc:
        raise HTTPException(404 if "不存在" in str(exc) else 422, str(exc))
    return {"record": record}

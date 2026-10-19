import asyncio

from fastapi import APIRouter, HTTPException

from .. import prompts
from .. import roles
from ..config import settings
from ..schemas import InterviewAnswerRequest, InterviewTurnRequest, TopQuestionsRequest
from ..services import llm as llm_service
from ..services import resume as resume_service
from ..services import question_bank as question_bank_service

router = APIRouter(prefix="/api/interview", tags=["interview"])

MAX_HISTORY_TURNS = 24
MAX_TOP_QUESTIONS = 10
TOP_QUESTIONS_TIMEOUT_SECONDS = 60.0


def _top_questions_channel() -> tuple[str | None, str | None]:
    configured_provider = settings.top_questions_provider.strip()
    configured_model = settings.top_questions_model.strip()
    if configured_provider:
        return configured_provider, configured_model or None
    if configured_model:
        return None, configured_model
    # 题目提纲需要逐道生成带参考答案的长文本。DeepSeek 的 flash 模型
    # 明显快于当前 ARK 默认模型，已配置 Key 时优先走这个通道。
    if settings.deepseek_api_key:
        return "deepseek", settings.deepseek_model or "deepseek-flash"
    if settings.llm_provider == "ark":
        return "ark", settings.ark_model or None
    return None, None


@router.get("/roles")
async def list_roles():
    return roles.list_roles()


@router.post("/turn")
async def interview_turn(req: InterviewTurnRequest):
    resume_profile = resume_service.resolve_profile_text(req.resume_id)
    role = roles.get_role(req.role_key)
    role_brief = roles.build_role_brief(role) if role else ""
    bank_brief = ""
    resume_focus = ""
    if role:
        # 真题来自用户自己的面试复盘文档，取不到时静默降级为模型自由出题。
        try:
            bank_brief = question_bank_service.build_question_bank_brief(
                question_bank_service.load_role_questions(role.key)
            )
        except Exception:
            bank_brief = ""
        # 题库覆盖不到的部分，靠简历里与本岗位相关的经历现场出定制题。
        try:
            resume_focus = roles.build_resume_focus(
                role, resume_service.resolve_profile_dict(req.resume_id)
            )
        except Exception:
            resume_focus = ""
    context = prompts.build_interviewer_context(
        req.jd,
        req.resume,
        req.knowledge,
        resume_profile,
        role_brief,
        bank_brief,
        resume_focus,
        company=req.company,
    )
    history = [item for item in req.history[-MAX_HISTORY_TURNS:] if item.content.strip()]
    remaining = req.max_questions - req.asked_count
    planned_questions = [
        question.strip()
        for question in req.planned_questions
        if question.strip()
    ][: req.max_questions]

    if not history:
        instruction = (
            "现在面试开始，请说开场白。严格遵守系统提示里的开场白规范："
            "先问好并自报身份，再用一句话点明本场面试主要聊哪几个方面，"
            "最后请候选人用两三分钟做自我介绍。"
            "这一轮不要提任何技术问题，也不要点名简历里的具体项目或数字。"
            "即使提供了题目提纲，这一轮也只做自我介绍，不要提提纲里的问题。"
            "方向最多说两个，全段不含标点不超过 80 字，写完自己数一遍，超了就删掉一个方向再输出。"
        )
    elif remaining <= 0:
        instruction = "提问数量已达上限，请说一段简短的结束语，并把 is_final 设为 true。"
    else:
        instruction = (
            f"请根据候选人最后的回答继续面试。还可以提问约 {remaining} 个主要问题。"
            "如果回答不够具体或缺少量化结果，请就该点追问。"
        )
        if planned_questions:
            next_index = min(req.asked_count, len(planned_questions) - 1)
            next_question = planned_questions[next_index]
            instruction += (
                "\n本场有固定题目提纲，必须按顺序提问。"
                f"如果上一题不需要追问，下一道主问题应为：{next_question}"
                "不要跳题，也不要新增提纲外的主问题。"
            )

    # 实测 DeepSeek 在 json_object 模式下，若历史包含多轮 assistant 消息会返回空白内容。
    # 因此把对话历史折叠成一段文本，始终只发送 system + 单条 user 消息。
    parts = [context]
    if planned_questions:
        parts.append(
            "【本场面试题目提纲（按顺序提问，已回答的不要重复）】\n"
            + "\n".join(
                f"{index + 1}. {question}"
                for index, question in enumerate(planned_questions)
            )
        )
    if history:
        transcript = "\n".join(
            ("面试官" if item.role == "interviewer" else "候选人") + "：" + item.content.strip()
            for item in history
        )
        parts.append("【已进行的对话】\n" + transcript)
    parts.append(instruction)

    messages: list[dict] = [
        {"role": "system", "content": prompts.INTERVIEWER_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]

    try:
        data = await asyncio.to_thread(llm_service.chat_json_messages, messages)
    except llm_service.LlmError as exc:
        raise HTTPException(502, str(exc))

    speech = str(data.get("speech") or "").strip()
    if not speech:
        raise HTTPException(502, "面试官未返回有效内容，请重试。")

    return {
        "speech": speech,
        # 开场白只是自我介绍引导，不计入本场主问题数量。
        "is_new_question": False if not history else bool(data.get("is_new_question", True)),
        "is_final": bool(data.get("is_final", False)) or remaining <= 0,
        "note": str(data.get("note") or ""),
    }


@router.post("/top-questions")
async def top_questions(req: TopQuestionsRequest):
    """按请求数量生成核心问题，供前端逐道追加成本场面试提纲。"""
    resume_profile = resume_service.resolve_profile_text(req.resume_id)
    role = roles.get_role(req.role_key)
    role_brief = roles.build_role_brief(role) if role else ""
    bank_brief = ""
    resume_focus = ""
    effective_jd = req.jd.strip()
    if role:
        if not effective_jd:
            effective_jd = role.default_jd
        try:
            bank_brief = question_bank_service.build_question_bank_brief(
                question_bank_service.load_role_questions(role.key)
            )
        except Exception:
            bank_brief = ""
        try:
            resume_focus = roles.build_resume_focus(
                role, resume_service.resolve_profile_dict(req.resume_id)
            )
        except Exception:
            resume_focus = ""
    context = prompts.build_interviewer_context(
        effective_jd,
        req.resume,
        req.knowledge,
        resume_profile,
        role_brief,
        bank_brief,
        resume_focus,
        company=req.company,
    )
    existing_questions = [
        question.strip()
        for question in req.existing_questions
        if question.strip()
    ]
    context_parts = [context, f"【本次生成数量】\n{req.count}"]
    if existing_questions:
        context_parts.append(
            "【已有题目（不要重复）】\n"
            + "\n".join(f"- {question}" for question in existing_questions)
        )
    context = "\n\n".join(context_parts)
    top_provider, top_model = _top_questions_channel()
    try:
        data = await asyncio.to_thread(
            llm_service.chat_json,
            prompts.TOP_QUESTIONS_SYSTEM,
            context,
            max_tokens=min(6000, max(3000, req.count * 700)),
            timeout=TOP_QUESTIONS_TIMEOUT_SECONDS,
            provider=top_provider,
            model=top_model,
        )
    except llm_service.LlmError as exc:
        raise HTTPException(502, str(exc))
    # 模型偶尔会多给、重复或漏字段；这里做一次归一化，保证页面拿到的是可直接渲染的题目。
    questions: list[dict] = []
    seen: set[str] = set(existing_questions)
    limit = min(req.count, MAX_TOP_QUESTIONS)
    for item in data.get("questions") or []:
        if not isinstance(item, dict):
            continue
        question_text = str(item.get("question") or "").strip()
        if not question_text or question_text in seen:
            continue
        seen.add(question_text)
        questions.append(
            {
                "question": question_text,
                "focus": str(item.get("focus") or "").strip(),
                "reference_answer": str(item.get("reference_answer") or "").strip(),
            }
        )
        if len(questions) == limit:
            break
    if not questions:
        raise HTTPException(502, "核心问题生成失败，未返回新的有效题目，请重试。")
    return {"questions": questions}


@router.post("/answer")
async def interview_answer(req: InterviewAnswerRequest):
    """候选人在模拟面试中卡壳时，为当前问题提供 40-60 秒参考答案。"""
    resume_profile = resume_service.resolve_profile_text(req.resume_id)
    role = roles.get_role(req.role_key)
    role_brief = roles.build_role_brief(role) if role else ""
    bank_brief = ""
    resume_focus = ""
    effective_jd = req.jd.strip()
    if role:
        if not effective_jd:
            effective_jd = role.default_jd
        try:
            bank_brief = question_bank_service.build_question_bank_brief(
                question_bank_service.load_role_questions(role.key)
            )
        except Exception:
            bank_brief = ""
        try:
            resume_focus = roles.build_resume_focus(
                role, resume_service.resolve_profile_dict(req.resume_id)
            )
        except Exception:
            resume_focus = ""
    context = prompts.build_interviewer_context(
        effective_jd,
        req.resume,
        req.knowledge,
        resume_profile,
        role_brief,
        bank_brief,
        resume_focus,
        company=req.company,
    )
    user_parts = [
        context,
        "【当前面试问题】\n" + req.question.strip(),
        "【候选人目前已说内容】\n" + (req.candidate_answer.strip() or "（尚未回答）"),
        "请生成这段参考答案。",
    ]
    messages: list[dict] = [
        {"role": "system", "content": prompts.INTERVIEW_ANSWER_SYSTEM},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    try:
        data = await asyncio.to_thread(
            llm_service.chat_json_messages,
            messages,
            temperature=0.5,
            max_tokens=4000,
        )
    except llm_service.LlmError as exc:
        raise HTTPException(502, str(exc))
    answer = str(data.get("answer") or "").strip()
    if not answer:
        raise HTTPException(502, "参考答案生成失败，请重试。")
    return {
        "answer": answer,
        "key_points": [
            str(item).strip()
            for item in (data.get("key_points") or [])
            if str(item).strip()
        ],
    }

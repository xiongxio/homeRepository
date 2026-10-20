import asyncio
import json
import re
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    AsyncOpenAI,
    OpenAI,
)

from ..config import settings


# LLM 调用日志，便于排查 MiniMax 等慢模型的超时/截断问题。
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
llm_logger = logging.getLogger("interview_assistant.llm")
if not llm_logger.handlers:
    _handler = RotatingFileHandler(
        LOG_DIR / "llm.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    llm_logger.addHandler(_handler)
    llm_logger.setLevel(logging.INFO)


class LlmError(RuntimeError):
    pass


class LlmLengthError(LlmError):
    """输出超过 max_tokens 被服务端截断。调用方可捕获后减少单批题量重试。"""


class LlmEmptyResponseError(LlmError):
    """服务端返回了 finish_reason=length 但正文为空，表示上游出现异常。

    不适合二分重试，调用方应当提前报错并提示用户切换模型。"""




# SDK 默认超时 600 秒且失败自动重试 2 次；上游卡住时前端会长时间转圈，
# 因此对组卷、对话等长任务显式设置有界超时，并关闭 SDK 层隐藏重试。
CHAT_TIMEOUT_SECONDS = 120.0
DIALOGUE_TIMEOUT_SECONDS = 60.0

# 上游 LLM 偶发连接抖动 / 5xx 时，单次失败就放弃会让整次组卷直接挂掉。
# 401/403/402/422 等业务类错误不在重试范围（重试也救不回来），
# 仅对连接异常 / 5xx 做有限退避重试。
_COMPLETION_MAX_ATTEMPTS = 3
_COMPLETION_RETRY_BACKOFF = 0.5


def _is_transient_upstream_error(exc: BaseException) -> bool:
    """判断错误是否为上游瞬时错误，决定是否重试。"""
    if isinstance(exc, (APIConnectionError, APITimeoutError, InternalServerError)):
        return True
    if isinstance(exc, APIStatusError):
        # 502/503/504 在当前 SDK 版本没有专门子类，但上游瞬时错误
        # 也值得重试。
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and status >= 500
    return False


def _provider_config(provider: str | None = None) -> tuple[str, str, str]:
    provider = provider or settings.llm_provider
    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise LlmError("未配置 DEEPSEEK_API_KEY。请在 backend/.env 中设置。")
        return settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model
    if provider == "ark":
        if not settings.ark_api_key:
            raise LlmError("未配置 ARK_API_KEY。请在模型配置页填写。")
        if not settings.ark_model:
            raise LlmError("未配置 ARK_MODEL。请填写火山方舟 Model ID 或接入点 ID。")
        return settings.ark_api_key, settings.ark_base_url, settings.ark_model
    if provider == "minimax":
        if not settings.minimax_api_key:
            raise LlmError("未配置 MINIMAX_API_KEY。请在模型配置页填写。")
        if not settings.minimax_model:
            raise LlmError("未配置 MINIMAX_MODEL。请填写 MiniMax 模型名。")
        return settings.minimax_api_key, settings.minimax_base_url, settings.minimax_model
    if provider == "openai":
        if not settings.openai_api_key:
            raise LlmError("未配置 OPENAI_API_KEY。请在 backend/.env 中设置。")
        return settings.openai_api_key, settings.openai_base_url, settings.openai_model
    raise LlmError(f"不支持的 LLM_PROVIDER: {provider}")


# 思考型模型（DeepSeek V4 系列等）把推理过程放在独立的 reasoning_content 字段，
# 它与正文共享同一个 max_tokens 预算。组卷这类任务正文本身就有四五千 token，
# 推理再吃掉几千，就会触发 finish_reason=length 且正文为空（content_len=0）。
#
# 实测（12K 字符资料 + 8 道题）：
#   思考开启 mt=8000   → 65s，正文 4989，推理占用 5020（正文随时可能被挤空）
#   思考开启 mt=32000  → 66s，正文 6076，推理占用 5255（加预算只会让推理更长）
#   思考关闭 mt=8000   → 16s，正文 5032，推理占用 0（又快又稳）
# 因此本项目的结构化 JSON 任务默认关闭思考；确有推理需要的调用方
# 可以显式传 extra_body 打开。
_REASONING_OFF_BODY = {
    "deepseek": {"thinking": {"type": "disabled"}},
}


def _reasoning_body(provider: str | None) -> dict | None:
    """返回该厂商默认的思考设置（关闭思考），不支持时返回 None。"""
    body = _REASONING_OFF_BODY.get(provider or "")
    return dict(body) if body else None


def _is_reasoning_overflow(resp) -> bool:
    """判断响应是否是"推理挤占了输出预算"：正文为空但推理内容有长度。

    这类情况不是上游限流，而是 max_tokens 被推理吃光，
    应当重试而不是直接让用户切模型。
    """
    try:
        choice = resp.choices[0]
    except (AttributeError, IndexError):
        return False
    content = getattr(choice.message, "content", None) or ""
    if content.strip():
        return False
    reasoning = getattr(choice.message, "reasoning_content", None) or ""
    return bool(reasoning.strip())


def _completion(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    max_attempts: int = _COMPLETION_MAX_ATTEMPTS,
    retry_timeout: bool = True,
    extra_body: dict | None = None,
):
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    use_json_mode = json_mode
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        is_last = attempt + 1 >= max_attempts
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_exc = exc
            text = str(exc).lower()
            # 1) JSON Mode 兼容性回退（不计重试预算）：服务端明确不支持 response_format 时
            #    退回普通补全，再试一次；回退仅生效一次。
            if use_json_mode and any(
                keyword in text
                for keyword in ("response_format", "json_object", "json mode", "unsupported parameter")
            ):
                if is_last:
                    raise
                use_json_mode = False
                kwargs.pop("response_format", None)
                continue
            # 2) 上游瞬时错误：连接异常 / 5xx —— 退避后重试。
            #    401/403/402/422 等业务错误走不到这里，原样抛出让 chat_json 包装。
            if _is_transient_upstream_error(exc):
                if isinstance(exc, APITimeoutError) and not retry_timeout:
                    raise
                if is_last:
                    raise
                backoff = _COMPLETION_RETRY_BACKOFF * (attempt + 1)
                llm_logger.warning(
                    "transient LLM error attempt=%d/%d type=%s backoff=%.1fs err=%s",
                    attempt + 1,
                    max_attempts,
                    type(exc).__name__,
                    backoff,
                    str(exc)[:200],
                )
                time.sleep(backoff)
                continue
            # 3) 其它错误（如 4xx 业务错）不重试，向上抛。
            raise
    assert last_exc is not None  # pragma: no cover - 不可达
    raise last_exc


async def _acompletion(
    client: AsyncOpenAI,
    *,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    extra_body: dict | None = None,
):
    """异步补全入口，错误分类、JSON 回退和重试策略与同步版本保持一致。"""
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    use_json_mode = json_mode
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    last_exc: Exception | None = None
    for attempt in range(_COMPLETION_MAX_ATTEMPTS):
        is_last = attempt + 1 >= _COMPLETION_MAX_ATTEMPTS
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_exc = exc
            text = str(exc).lower()
            if use_json_mode and any(
                keyword in text
                for keyword in ("response_format", "json_object", "json mode", "unsupported parameter")
            ):
                if is_last:
                    raise
                use_json_mode = False
                kwargs.pop("response_format", None)
                continue
            if _is_transient_upstream_error(exc):
                if is_last:
                    raise
                backoff = _COMPLETION_RETRY_BACKOFF * (attempt + 1)
                llm_logger.warning(
                    "transient LLM error attempt=%d/%d type=%s backoff=%.1fs err=%s",
                    attempt + 1,
                    _COMPLETION_MAX_ATTEMPTS,
                    type(exc).__name__,
                    backoff,
                    str(exc)[:200],
                )
                await asyncio.sleep(backoff)
                continue
            raise
    assert last_exc is not None  # pragma: no cover - 不可达
    raise last_exc


def chat_json(
    system: str,
    user: str,
    max_tokens: int = 8000,
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    max_attempts: int = _COMPLETION_MAX_ATTEMPTS,
    retry_timeout: bool = True,
) -> dict:
    api_key, base_url, resolved_model = _provider_config(provider)
    provider_name = provider or settings.llm_provider
    if timeout is None:
        if provider_name == "minimax":
            try:
                timeout = float(getattr(settings, "minimax_timeout_seconds", 180))
            except (TypeError, ValueError):
                timeout = 180.0
        else:
            timeout = CHAT_TIMEOUT_SECONDS
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    )
    llm_logger.info(
        "call start provider=%s model=%s max_tokens=%d timeout=%.1f",
        provider_name,
        model or resolved_model,
        max_tokens,
        timeout,
    )
    start_ts = time.perf_counter()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    # 结构化 JSON 任务默认关闭思考，避免推理把输出预算吃光。
    reasoning_body = _reasoning_body(provider_name)
    for attempt in range(2):
        try:
            resp_iter = _completion(
                client,
                model=model or resolved_model,
                messages=messages,
                temperature=0.3 if attempt == 0 else 0.2,
                max_tokens=max_tokens,
                json_mode=True,
                max_attempts=max_attempts,
                retry_timeout=retry_timeout,
                extra_body=reasoning_body,
            )
        except Exception as exc:
            raise LlmError(_classify_upstream_error(exc) or f"调用 LLM 失败: {exc}") from exc
        choice_iter = resp_iter.choices[0]
        content_iter = choice_iter.message.content or ""
        elapsed = time.perf_counter() - start_ts
        llm_logger.info(
            "call attempt=%d provider=%s model=%s elapsed=%.2f finish=%s content_len=%d",
            attempt,
            provider_name,
            model or resolved_model,
            elapsed,
            choice_iter.finish_reason,
            len(content_iter),
        )
        if choice_iter.finish_reason == "length":
            if not content_iter.strip():
                # 推理挤占了输出预算：去掉思考设置重试一次，
                # 真正的上游异常（限流等）才会走到下面的报错。
                if reasoning_body and attempt == 0 and _is_reasoning_overflow(resp_iter):
                    llm_logger.info(
                        "reasoning overflow detected, retry without thinking override "
                        "provider=%s model=%s",
                        provider_name,
                        model or resolved_model,
                    )
                    reasoning_body = None
                    continue
                raise LlmEmptyResponseError(
                    "模型返回了空内容且 finish_reason=length，上游可能出现异常（限流、账号异常等）。请稍后重试或切换其它模型。"
                )
            raise LlmLengthError(
                "模型输出达到长度上限被截断，返回的 JSON 不完整。系统将自动减少单批题量重试，不需手动缩少输入。"
            )
        try:
            return _parse_json(content_iter)
        except LlmError as exc:
            last_error = exc
            # 把失败输出也交给模型，再追加一条只输出严格 JSON 的指令。
            messages = messages + [
                {"role": "assistant", "content": content_iter},
                {
                    "role": "user",
                    "content": (
                        "上面输出无法解析为 JSON，请只输出严格合法的 JSON，"
                        "不要包含<think>、解释、注释或前后缀文字。"
                    ),
                }
            ]
    llm_logger.warning(
        "call failed provider=%s model=%s last_error=%s",
        provider_name,
        model or resolved_model,
        str(last_error)[:200] if last_error else "",
    )
    raise LlmError(str(last_error) if last_error else "LLM 返回内容无法解析为 JSON。")


async def chat_json_async(
    system: str,
    user: str,
    max_tokens: int = 8000,
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> dict:
    """异步版 JSON 调用，支持随请求取消并立即中断上游 HTTP 请求。"""
    api_key, base_url, resolved_model = _provider_config(provider)
    provider_name = provider or settings.llm_provider
    if timeout is None:
        if provider_name == "minimax":
            try:
                timeout = float(getattr(settings, "minimax_timeout_seconds", 180))
            except (TypeError, ValueError):
                timeout = 180.0
        else:
            timeout = CHAT_TIMEOUT_SECONDS
    llm_logger.info(
        "async call start provider=%s model=%s max_tokens=%d timeout=%.1f",
        provider_name,
        model or resolved_model,
        max_tokens,
        timeout,
    )
    start_ts = time.perf_counter()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    reasoning_body = _reasoning_body(provider_name)
    async with AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    ) as client:
        for attempt in range(2):
            try:
                resp_iter = await _acompletion(
                    client,
                    model=model or resolved_model,
                    messages=messages,
                    temperature=0.3 if attempt == 0 else 0.2,
                    max_tokens=max_tokens,
                    json_mode=True,
                    extra_body=reasoning_body,
                )
            except Exception as exc:
                raise LlmError(_classify_upstream_error(exc) or f"调用 LLM 失败: {exc}") from exc
            choice_iter = resp_iter.choices[0]
            content_iter = choice_iter.message.content or ""
            elapsed = time.perf_counter() - start_ts
            llm_logger.info(
                "async call attempt=%d provider=%s model=%s elapsed=%.2f finish=%s content_len=%d",
                attempt,
                provider_name,
                model or resolved_model,
                elapsed,
                choice_iter.finish_reason,
                len(content_iter),
            )
            if choice_iter.finish_reason == "length":
                if not content_iter.strip():
                    # 推理挤占了输出预算：去掉思考设置重试一次。
                    if reasoning_body and attempt == 0 and _is_reasoning_overflow(resp_iter):
                        llm_logger.info(
                            "reasoning overflow detected, retry without thinking override "
                            "provider=%s model=%s",
                            provider_name,
                            model or resolved_model,
                        )
                        reasoning_body = None
                        continue
                    raise LlmEmptyResponseError(
                        "模型返回了空内容且 finish_reason=length，上游可能出现异常（限流、账号异常等）。请稍后重试或切换其它模型。"
                    )
                raise LlmLengthError(
                    "模型输出达到长度上限被截断，返回的 JSON 不完整。系统将自动减少单批题量重试，不需手动缩少输入。"
                )
            try:
                return _parse_json(content_iter)
            except LlmError as exc:
                last_error = exc
                messages = messages + [
                    {"role": "assistant", "content": content_iter},
                    {
                        "role": "user",
                        "content": (
                            "上面输出无法解析为 JSON，请只输出严格合法的 JSON，"
                            "不要包含<think>、解释、注释或前后缀文字。"
                        ),
                    },
                ]
    llm_logger.warning(
        "async call failed provider=%s model=%s last_error=%s",
        provider_name,
        model or resolved_model,
        str(last_error)[:200] if last_error else "",
    )
    raise LlmError(str(last_error) if last_error else "LLM 返回内容无法解析为 JSON。")


def chat_json_messages(
    messages: list[dict],
    temperature: float = 0.6,
    max_tokens: int = 2000,
    retries: int = 2,
) -> dict:
    """多轮对话版本，用于模拟面试官逐轮提问。"""
    api_key, base_url, model = _provider_config()
    provider_name = settings.llm_provider
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=DIALOGUE_TIMEOUT_SECONDS,
        max_retries=0,
    )
    last_error: Exception | None = None
    # 模拟面试对话需要推理能力，默认保留厂商默认思考设置；
    # 但 max_tokens 只有 2000，推理极易挤空正文，溢出时关闭思考重试。
    reasoning_body = None
    fallback_body = _REASONING_OFF_BODY.get(provider_name)
    for attempt in range(retries + 1):
        try:
            resp = _completion(
                client,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
                extra_body=reasoning_body,
            )
        except Exception as exc:
            raise LlmError(_classify_upstream_error(exc) or f"调用 LLM 失败: {exc}") from exc
        choice = resp.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length":
            # 截断的 JSON 无论重试几次都解析不了，直接报出真实原因。
            if not content.strip():
                # 对话场景 max_tokens 较小，推理更容易挤空正文，关掉思考重试。
                if fallback_body and attempt == 0 and _is_reasoning_overflow(resp):
                    llm_logger.info(
                        "reasoning overflow detected, retry with thinking disabled "
                        "provider=%s model=%s",
                        provider_name,
                        model,
                    )
                    reasoning_body = fallback_body
                    continue
                raise LlmEmptyResponseError(
                    "模型返回了空内容且 finish_reason=length，上游可能出现异常（限流、账号异常等）。请稍后重试或切换其它模型。"
                )
            raise LlmLengthError(
                "模型输出达到长度上限被截断，返回的 JSON 不完整。系统将自动减少单批题量重试，不需手动缩少输入。"
            )
        if not content.strip():
            # 空白回复通常意味着对话结构异常（例如连续同角色消息）。
            last_error = LlmError("LLM 返回了空内容，请检查对话消息结构。")
            if attempt == retries:
                break
            continue
        try:
            return _parse_json(content)
        except LlmError as exc:
            # 模型偶发返回非严格 JSON，重试一次通常即可恢复。
            last_error = exc
            if attempt == retries:
                break
    raise LlmError(str(last_error) if last_error else "LLM 返回内容无法解析为 JSON。")


def _parse_json(content: str) -> dict:
    # MiniMax 等思考型模型会在正式 JSON 前先输出 <think>...</think>，
    # 解析前先把这部分去掉，避免被误判成非 JSON。
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 常见小毛病：尾随逗号、Markdown 残留，先修复再尝试解析。
    repaired = re.sub(r",(\s*[}\]])", r"\1", content)
    if repaired != content:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    # 逐字符扫描出第一个完整的顶层 JSON 对象，容忍尾部多余内容。
    start = content.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(content)):
            char = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(content[start : index + 1])
                    except json.JSONDecodeError:
                        break
    raise LlmError("LLM 返回内容无法解析为 JSON，请重试。")


def _classify_upstream_error(exc: BaseException) -> str | None:
    """把上游 LLM 错误（余额/鉴权/限流）翻译成稳定的中文提示。

    提示文本以 `LLM_UPSTREAM:` 前缀开头，前端/路由可据此判定这是上游问题
    （应返回 502），而不是请求参数问题（应返回 422）。
    """
    text = str(exc) or ""
    lowered = text.lower()
    if (
        "insufficient balance" in lowered
        or "insufficient_balance" in lowered
        or "余额不足" in text
        or "insufficient quota" in lowered
        or "quota exceeded" in lowered
    ):
        return "LLM_UPSTREAM: 上游账户余额不足，请充值或切换 LLM 接入后再试。"
    if (
        "invalid api key" in lowered
        or "incorrect api key" in lowered
        or "authentication" in lowered
        or "unauthorized" in lowered
        or "401" in text
    ):
        return "LLM_UPSTREAM: LLM 鉴权失败，请检查 API Key 是否正确或已过期。"
    if "permission" in lowered or "forbidden" in lowered or "403" in text:
        return "LLM_UPSTREAM: LLM 拒绝访问，请确认该 Key 有目标模型权限。"
    if "rate limit" in lowered or "too many requests" in lowered or "429" in text:
        return "LLM_UPSTREAM: LLM 接口触发限流，请稍后重试。"
    return None


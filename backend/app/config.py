from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM 提供方：deepseek | ark | minimax | openai
    llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # 旧别名 deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用，
    # 当前官方可用 ID 只有 deepseek-flash 与 deepseek-v4-pro。
    deepseek_model: str = "deepseek-flash"
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_model: str = "doubao-seed-2-1-pro-260628"
    # 核心问题带参考答案，输出较长；可单独指定更快的生成通道。
    top_questions_provider: str = ""
    top_questions_model: str = ""
    minimax_api_key: str = ""
    # 国内区接口域名是 api.minimaxi.com（多一个 i），国际区是 api.minimax.io。
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    # MiniMax-M2 已被官方归入 Legacy，当前主力模型是 M3（1M 上下文）。
    minimax_model: str = "MiniMax-M3"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # 可选：给复习分析单独换更快/更便宜的模型；留空则使用当前全局模型。
    review_model: str = ""
    # MiniMax 会先输出 <think> 推理，JSON 推理单独 token 预算需要放大、批数更小。
    minimax_max_tokens: int = 16000
    minimax_batch_size: int = 6
    minimax_timeout_seconds: int = 180

    # 语音转写
    # local：本地 faster-whisper，无需 API Key（默认）
    # openai：调用 OpenAI Whisper，需要 openai_api_key
    asr_backend: str = "local"
    # tiny / base / small / medium / large-v3，越大越准也越慢
    # auto：有 GPU 用 medium，否则用 small；也可显式指定 tiny/base/small/medium/large-v3
    asr_model: str = "auto"
    # auto：有 GPU 用 GPU，否则 CPU；也可强制 cuda / cpu
    asr_device: str = "auto"
    asr_compute_type: str = "float16"
    whisper_model: str = "whisper-1"
    # 留空自动检测语言；也可设为 zh / en
    asr_language: str = ""

    # 上传
    max_upload_size_mb: int = 2048
    upload_chunk_mb: int = 8

    # OpenAI Whisper API 单次请求硬限制 25MB，留安全余量
    whisper_max_bytes: int = 24 * 1024 * 1024

    data_dir: Path = Path(__file__).resolve().parent.parent / "data"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

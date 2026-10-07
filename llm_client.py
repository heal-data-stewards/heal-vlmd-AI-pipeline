"""Shared LLM client for VLMD conversion pipeline.

Supports Azure AI Foundry (primary for HEAL) and Anthropic (alternative).
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI

load_dotenv(Path(__file__).parent / ".env")

MODELS = {
    # ── Azure AI Foundry ─────────────────────────────────────────────────────
    "azure-gpt-4.1-mini": {
        "provider": "azure",
        "model_id": "gpt-4.1-mini",
        "supports_temperature": True,
    },
    "azure-gpt-5.4-mini": {
        "provider": "azure",
        "model_id": "gpt-5.4-mini",
        "supports_temperature": False,
        "use_max_completion_tokens": True,
    },
    "azure-gpt-5.4": {
        "provider": "azure",
        "model_id": "gpt-5.4",
        "supports_temperature": False,
        "use_max_completion_tokens": True,
    },
    "azure-gpt-5.5": {
        "provider": "azure",
        "model_id": "gpt-5.5",
        "supports_temperature": False,
        "use_max_completion_tokens": True,
    },
    "azure-gpt-chat-latest": {
        "provider": "azure",
        "model_id": "gpt-chat-latest",
        "supports_temperature": False,
        "use_max_completion_tokens": True,
    },
    "azure-deepseek-v4-pro": {
        "provider": "azure",
        "model_id": "DeepSeek-V4-Pro",
        "supports_temperature": True,
    },
    # ── Anthropic ────────────────────────────────────────────────────────────
    "claude-haiku": {
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "supports_temperature": True,
    },
    "claude-sonnet": {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "supports_temperature": True,
    },
}

DEFAULT_MODEL = "azure-gpt-4.1-mini"

_azure_client = None
_anthropic_client = None


def _get_azure_client():
    global _azure_client
    if _azure_client is None:
        key = os.getenv("AZURE_OPENAI_API_KEY", "")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        if not key or not endpoint:
            raise ValueError(
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set in .env"
            )
        # Endpoints ending in /v1 are the OpenAI-compatible path — use the
        # standard OpenAI client with base_url (no api-version needed).
        # Classic .openai.azure.com or .services.ai.azure.com endpoints use
        # AzureOpenAI; strip any trailing /openai to avoid doubling the path.
        if endpoint.endswith("/v1"):
            _azure_client = OpenAI(
                api_key=key,
                base_url=endpoint,
                timeout=120.0,
                max_retries=2,
            )
        else:
            if endpoint.endswith("/openai"):
                endpoint = endpoint[: -len("/openai")]
            version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            _azure_client = AzureOpenAI(
                api_key=key, azure_endpoint=endpoint,
                api_version=version, timeout=120.0, max_retries=2,
            )
    return _azure_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        try:
            import anthropic as _anthropic_lib
        except ImportError:
            raise ImportError("anthropic package not installed — run: pip install anthropic")
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        _anthropic_client = _anthropic_lib.Anthropic(api_key=key)
    return _anthropic_client


def _call_azure(system: str, user: str, model_cfg: dict,
                max_tokens: int, temperature: float) -> tuple[str, bool, str]:
    try:
        c = _get_azure_client()
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs = {"model": model_cfg["model_id"], "messages": msgs}
        if model_cfg.get("supports_temperature", True):
            kwargs["temperature"] = temperature
        if "reasoning_effort" in model_cfg:
            kwargs["reasoning_effort"] = model_cfg["reasoning_effort"]
            kwargs["max_completion_tokens"] = max_tokens
        elif model_cfg.get("use_max_completion_tokens"):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        resp = c.chat.completions.create(**kwargs)
        return resp.choices[0].message.content.strip(), True, ""
    except Exception as e:
        return "", False, str(e)


def _call_anthropic(system: str, user: str, model_cfg: dict,
                    max_tokens: int, temperature: float) -> tuple[str, bool, str]:
    try:
        c = _get_anthropic_client()
        kwargs = {
            "model": model_cfg["model_id"],
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if model_cfg.get("supports_temperature", True):
            kwargs["temperature"] = temperature
        resp = c.messages.create(**kwargs)
        return resp.content[0].text.strip(), True, ""
    except Exception as e:
        return "", False, str(e)


def call_llm(system: str, user: str, model_key: str = DEFAULT_MODEL,
             max_tokens: int = 4096, temperature: float = 0.0) -> tuple[str, bool, str]:
    """Call an LLM. Returns (text, success, error_message)."""
    if model_key not in MODELS:
        return "", False, f"Unknown model: {model_key!r}. Available: {list(MODELS)}"
    model_cfg = MODELS[model_key]
    if model_cfg["provider"] == "azure":
        return _call_azure(system, user, model_cfg, max_tokens, temperature)
    if model_cfg["provider"] == "anthropic":
        return _call_anthropic(system, user, model_cfg, max_tokens, temperature)
    return "", False, f"Unknown provider: {model_cfg['provider']!r}"


def parse_json_response(text: str):
    """Strip code fences and parse JSON."""
    s = text.strip()
    for fence in ["```json", "```"]:
        if s.startswith(fence):
            s = s[len(fence):].strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    return json.loads(s)

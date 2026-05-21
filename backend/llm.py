"""
LLM Factory — единственное место выбора LLM.

Провайдеры (устанавливается через LLM_PROVIDER в .env):
  gemini    → Google Gemini (бесплатно, нужен GOOGLE_API_KEY, рекомендуется)
  groq      → Groq (бесплатно, нужен GROQ_API_KEY)
  gigachat  → GigaChat (нужен GIGACHAT_AUTH_KEY, FREE для физ.лиц)
  ollama    → Ollama local (бесплатно, без ключа, нужен запущенный ollama)
  anthropic → Claude (платный, нужен ANTHROPIC_API_KEY)

Все провайдеры поддерживают bind_tools() для Campaign Builder (F2).

Рекомендации:
  - Gemini:  aistudio.google.com → Get API Key → бесплатный tier
             gemini-2.5-flash (10 RPM / 250 RPD) или
             gemini-2.5-flash-lite (15 RPM / 1000 RPD — если упираемся в RPD)
  - Groq:    console.groq.com → API Keys → бесплатный tier, llama-3.3-70b
             (низкий TPD; для большего объёма — llama-3.1-8b-instant)
  - Ollama:  brew install ollama && ollama pull qwen2.5:7b && ollama serve
"""

import importlib.util
import json
import os
from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


PROVIDER_MODULES: dict[str, tuple[str, ...]] = {
    "gigachat": ("langchain_gigachat", "langchain_community"),
    "groq": ("langchain_groq", "httpx"),
    "gemini": ("langchain_google_genai",),
    "ollama": ("langchain_ollama",),
    "anthropic": ("langchain_anthropic",),
}


def _has_any_module(module_names: tuple[str, ...]) -> bool:
    return any(importlib.util.find_spec(module_name) is not None for module_name in module_names)


def _provider_is_installed(provider: str) -> bool:
    return _has_any_module(PROVIDER_MODULES.get(provider, ()))


def _provider_candidates() -> list[str]:
    """Returns configured providers whose integration packages are installed.

    Порядок = приоритет автовыбора, если LLM_PROVIDER не задан.
    Gemini первым — на free tier у него самая щедрая квота (250k TPM, 250 RPD
    на 2.5-flash) и стабильная поддержка русского.
    """
    candidates: list[str] = []
    if os.getenv("GOOGLE_API_KEY"):
        candidates.append("gemini")
    if os.getenv("ANTHROPIC_API_KEY"):
        candidates.append("anthropic")
    if os.getenv("GROQ_API_KEY"):
        candidates.append("groq")
    if os.getenv("GIGACHAT_AUTH_KEY"):
        candidates.append("gigachat")
    candidates.append("ollama")
    return [provider for provider in candidates if _provider_is_installed(provider)]


def get_llm(for_tools: bool = False, temperature: float | None = None) -> BaseChatModel:
    """Возвращает LLM согласно настройкам окружения.

    temperature: опционально перекрывает температуру по умолчанию (0.05).
    """
    provider = os.getenv("LLM_PROVIDER", "").lower().strip()

    if not provider:
        candidates = _provider_candidates()
        provider = candidates[0] if candidates else "ollama"

    dispatch = {
        "gigachat":  _gigachat,
        "groq":      _groq,
        "gemini":    _gemini,
        "ollama":    _ollama,
        "anthropic": _anthropic,
    }

    fn = dispatch.get(provider)
    if fn is None:
        raise ValueError(
            f"Неизвестный LLM провайдер: '{provider}'. "
            f"Допустимые значения: {', '.join(dispatch.keys())}"
        )

    if not _provider_is_installed(provider):
        fallback_provider = next((candidate for candidate in _provider_candidates() if candidate != provider), None)
        if fallback_provider:
            print(
                f"[llm] Provider '{provider}' is configured, but its package is not installed; "
                f"fallback → {fallback_provider}"
            )
            return dispatch[fallback_provider](for_tools=for_tools, temperature=temperature)
        modules = ", ".join(PROVIDER_MODULES.get(provider, ()))
        raise RuntimeError(
            f"LLM provider '{provider}' is configured, but required package is not installed: {modules}. "
            "Install dependencies from backend/requirements.txt or choose another LLM_PROVIDER."
        )

    return fn(for_tools=for_tools, temperature=temperature)


def _temperature(override: float | None) -> float:
    return 0.05 if override is None else override


# ── GigaChat ──────────────────────────────────────────────────────────────────

def _gigachat(for_tools: bool = False, temperature: float | None = None) -> BaseChatModel:
    try:
        from langchain_gigachat import GigaChat
    except ImportError:
        from langchain_community.chat_models import GigaChat  # type: ignore

    model = os.getenv("GIGACHAT_TOOL_MODEL" if for_tools else "GIGACHAT_CHAT_MODEL", "GigaChat")
    auth_key  = os.getenv("GIGACHAT_AUTH_KEY", "")
    scope     = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    base_url  = os.getenv("GIGACHAT_API_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1")
    verify_ssl = os.getenv("GIGACHAT_ALLOW_INSECURE_TLS", "false").lower() != "true"

    print(f"[llm] GigaChat → {model} (tools={for_tools})")
    return GigaChat(
        credentials=auth_key,
        scope=scope,
        model=model,
        base_url=base_url,
        profanity_check=False,
        verify_ssl_certs=verify_ssl,
        timeout=120,
        temperature=_temperature(temperature),
    )


# ── Groq (FREE tier, recommended) ─────────────────────────────────────────────

def _groq(for_tools: bool = False, temperature: float | None = None) -> BaseChatModel:
    default_model = "llama-3.3-70b-versatile"
    model = os.getenv("GROQ_MODEL", default_model)
    api_key = os.getenv("GROQ_API_KEY", "")
    max_tokens = int(os.getenv("GROQ_MAX_TOKENS", "2048"))
    temp = _temperature(temperature)

    try:
        from langchain_groq import ChatGroq

        print(f"[llm] Groq → {model} (tools={for_tools}, langchain_groq)")
        return ChatGroq(
            model=model,
            api_key=api_key,
            temperature=temp,
            max_tokens=max_tokens,
        )
    except ImportError:
        print(f"[llm] Groq → {model} (tools={for_tools}, native httpx adapter)")
        return GroqNativeChatModel(model=model, api_key=api_key, max_tokens=max_tokens, temperature=temp)


# ── Native Groq fallback (OpenAI-compatible API via httpx) ────────────────────

class GroqNativeChatModel(BaseChatModel):
    """Minimal LangChain chat model for Groq without langchain_groq installed."""

    _model: str = PrivateAttr(default="")
    _api_key: str = PrivateAttr(default="")
    _max_tokens: int = PrivateAttr(default=2048)
    _temperature: float = PrivateAttr(default=0.05)
    _bound_tools: list[Any] = PrivateAttr(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, *, model: str, api_key: str, max_tokens: int = 2048, temperature: float = 0.05, **kwargs: Any):
        super().__init__(**kwargs)
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = temperature

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "GroqNativeChatModel":
        new = GroqNativeChatModel(model=self._model, api_key=self._api_key, max_tokens=self._max_tokens, temperature=self._temperature)
        new._bound_tools = list(tools)
        return new

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        import httpx

        if not self._api_key:
            raise RuntimeError("GROQ_API_KEY is empty; set it in backend/.env")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_to_openai_message(message) for message in messages],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if stop:
            payload["stop"] = stop
        if self._bound_tools:
            payload["tools"] = [_to_openai_tool(tool) for tool in self._bound_tools]
            payload["tool_choice"] = "auto"

        with httpx.Client(timeout=120, trust_env=False) as client:
            response = client.post(
                os.getenv("GROQ_API_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        ai_message = _from_openai_assistant_message(message)
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        import asyncio
        return await asyncio.to_thread(self._generate, messages, stop=stop, **kwargs)

    @property
    def _llm_type(self) -> str:
        return "groq-native"


def _to_openai_tool(tool: Any) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    if hasattr(tool, "args_schema") and tool.args_schema:
        try:
            schema = tool.args_schema.model_json_schema()
        except Exception:
            schema = {"type": "object", "properties": {}}
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or tool.name,
            "parameters": schema,
        },
    }


def _to_openai_message(message: BaseMessage) -> dict[str, Any]:
    content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": content}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": content}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "content": content,
            "tool_call_id": getattr(message, "tool_call_id", None) or getattr(message, "id", None) or "tool_call",
        }
    if isinstance(message, AIMessage):
        result: dict[str, Any] = {"role": "assistant", "content": content or None}
        if getattr(message, "tool_calls", None):
            result["tool_calls"] = [
                {
                    "id": call.get("id") or f"call_{call.get('name', 'tool')}",
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("args", {}), ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return result
    return {"role": "user", "content": content}


def _from_openai_assistant_message(message: dict[str, Any]) -> AIMessage:
    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"raw": function.get("arguments") or ""}
        tool_calls.append(ToolCall(
            id=call.get("id") or f"call_{function.get('name', 'tool')}",
            name=function.get("name", "tool"),
            args=args,
        ))
    return AIMessage(content=message.get("content") or "", tool_calls=tool_calls)


# ── Google Gemini (FREE tier) ─────────────────────────────────────────────────

def _gemini(for_tools: bool = False, temperature: float | None = None) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    # gemini-1.5-flash официально deprecated в 2026 — по умолчанию ставим 2.5-flash.
    # GOOGLE_TOOL_MODEL опционально перебивает модель для запросов с bind_tools()
    # (на случай, если для tool use хочется отдельную модель).
    default_model = "gemini-2.5-flash"
    env_key = "GOOGLE_TOOL_MODEL" if for_tools else "GOOGLE_MODEL"
    model = os.getenv(env_key) or os.getenv("GOOGLE_MODEL", default_model)
    api_key = os.getenv("GOOGLE_API_KEY", "")
    max_tokens = int(os.getenv("GOOGLE_MAX_TOKENS", "2048"))

    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY пустой; получи ключ в https://aistudio.google.com "
            "и положи в backend/.env как GOOGLE_API_KEY=..."
        )

    print(f"[llm] Gemini → {model} (tools={for_tools})")
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=_temperature(temperature),
        max_output_tokens=max_tokens,
    )


# ── Ollama (local, completely free) ───────────────────────────────────────────

def _ollama(for_tools: bool = False, temperature: float | None = None) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    default_model = "qwen2.5:7b"
    model = os.getenv("OLLAMA_MODEL", default_model)
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    print(f"[llm] Ollama → {model} @ {base_url} (tools={for_tools})")
    return ChatOllama(
        model=model,
        base_url=base_url,
        temperature=_temperature(temperature),
        num_predict=2048,
    )


# ── Anthropic Claude ──────────────────────────────────────────────────────────

def _anthropic(for_tools: bool = False, temperature: float | None = None) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    print(f"[llm] Anthropic → {model} (tools={for_tools})")
    return ChatAnthropic(model=model, temperature=_temperature(temperature))

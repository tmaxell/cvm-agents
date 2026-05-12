"""
LLM Factory — единственное место выбора LLM.

Провайдеры (устанавливается через LLM_PROVIDER в .env):
  gigachat  → GigaChat (по умолчанию, нужен GIGACHAT_AUTH_KEY)
  groq      → Groq (бесплатно, нужен GROQ_API_KEY, рекомендуется)
  gemini    → Google Gemini (бесплатно, нужен GOOGLE_API_KEY)
  ollama    → Ollama local (бесплатно, без ключа, нужен запущенный ollama)
  anthropic → Claude (нужен ANTHROPIC_API_KEY)

Все провайдеры поддерживают bind_tools() для Campaign Builder (F2).

Рекомендации:
  - Groq:    console.groq.com → API Keys → бесплатный tier, llama-3.3-70b
  - Gemini:  aistudio.google.com → Get API Key → бесплатный tier, gemini-1.5-flash
  - Ollama:  brew install ollama && ollama pull qwen2.5:7b && ollama serve
"""

import os
from langchain_core.language_models import BaseChatModel


def get_llm(for_tools: bool = False) -> BaseChatModel:
    """Возвращает LLM согласно настройкам окружения."""
    provider = os.getenv("LLM_PROVIDER", "").lower().strip()

    # Auto-detect if not set
    if not provider:
        if os.getenv("GROQ_API_KEY"):
            provider = "groq"
        elif os.getenv("GOOGLE_API_KEY"):
            provider = "gemini"
        elif os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("GIGACHAT_AUTH_KEY"):
            provider = "gigachat"
        else:
            provider = "ollama"

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
    return fn(for_tools=for_tools)


# ── GigaChat ──────────────────────────────────────────────────────────────────

def _gigachat(for_tools: bool = False) -> BaseChatModel:
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
        temperature=0.05,
    )


# ── Groq (FREE tier, recommended) ─────────────────────────────────────────────

def _groq(for_tools: bool = False) -> BaseChatModel:
    from langchain_groq import ChatGroq

    # llama-3.3-70b-versatile has excellent tool use support on free tier
    default_model = "llama-3.3-70b-versatile"
    model = os.getenv("GROQ_MODEL", default_model)
    api_key = os.getenv("GROQ_API_KEY", "")
    max_tokens = int(os.getenv("GROQ_MAX_TOKENS", "2048"))

    print(f"[llm] Groq → {model} (tools={for_tools})")
    return ChatGroq(
        model=model,
        api_key=api_key,
        temperature=0.05,
        max_tokens=max_tokens,
    )


# ── Google Gemini (FREE tier) ─────────────────────────────────────────────────

def _gemini(for_tools: bool = False) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    # gemini-1.5-flash: free tier, fast, good tool use
    default_model = "gemini-1.5-flash"
    model = os.getenv("GOOGLE_MODEL", default_model)
    api_key = os.getenv("GOOGLE_API_KEY", "")

    print(f"[llm] Gemini → {model} (tools={for_tools})")
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=0.05,
    )


# ── Ollama (local, completely free) ───────────────────────────────────────────

def _ollama(for_tools: bool = False) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    # qwen2.5:7b has good tool use; llama3.2 is lighter; mistral-nemo is fast
    default_model = "qwen2.5:7b"
    model = os.getenv("OLLAMA_MODEL", default_model)
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    print(f"[llm] Ollama → {model} @ {base_url} (tools={for_tools})")
    return ChatOllama(
        model=model,
        base_url=base_url,
        temperature=0.05,
        num_predict=2048,
    )


# ── Anthropic Claude ──────────────────────────────────────────────────────────

def _anthropic(for_tools: bool = False) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    print(f"[llm] Anthropic → {model} (tools={for_tools})")
    return ChatAnthropic(model=model)

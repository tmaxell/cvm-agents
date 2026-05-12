"""
GigaChatWithTools — GigaChat с нативным function calling.

langchain_community.GigaChat не реализует bind_tools() (NotImplementedError).
GigaChat SDK 0.2.x нативно поддерживает functions + function_call в Chat API.
Этот класс добавляет LangChain-совместимый bind_tools() через нативный API.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage, BaseMessage, ToolCall, ToolMessage,
    HumanMessage, SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr


class GigaChatWithTools(BaseChatModel):
    """GigaChat с поддержкой tool use через нативный function calling API."""

    # Pydantic v2: приватные атрибуты — не в схеме
    _base_llm: Any = PrivateAttr(default=None)
    _bound_tools: list = PrivateAttr(default_factory=list)
    _tool_map: dict = PrivateAttr(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def create(cls, llm: Any) -> "GigaChatWithTools":
        instance = cls()
        instance._base_llm = llm
        return instance

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "GigaChatWithTools":
        """Возвращает копию с зафиксированными инструментами."""
        new = GigaChatWithTools.create(self._base_llm)
        new._bound_tools = list(tools)
        new._tool_map = {t.name: t for t in tools if hasattr(t, "name")}
        return new

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        from gigachat.models import (
            Chat, Messages, MessagesRole,
            Function, FunctionParameters, FunctionParametersProperty,
        )

        # Конвертируем tools → GigaChat Function schema
        gc_functions = []
        for tool in self._bound_tools:
            schema = {}
            if hasattr(tool, "args_schema") and tool.args_schema:
                try:
                    schema = tool.args_schema.model_json_schema()
                except Exception:
                    pass
            properties = {}
            for p_name, p_info in schema.get("properties", {}).items():
                properties[p_name] = FunctionParametersProperty(
                    type_=p_info.get("type", "string"),
                    description=p_info.get("description", p_info.get("title", p_name)),
                )
            gc_functions.append(Function(
                name=tool.name,
                description=tool.description or tool.name,
                parameters=FunctionParameters(
                    properties=properties,
                    required=schema.get("required", []),
                ) if properties else None,
            ))

        # LangChain messages → GigaChat Messages
        gc_messages = _to_gc_messages(messages)

        # Вызов GigaChat API
        chat_payload = Chat(
            model=self._base_llm.model,
            messages=gc_messages,
            temperature=0.05,
        )
        if gc_functions:
            chat_payload.functions = gc_functions
            chat_payload.function_call = "auto"

        resp = self._base_llm.client.chat(chat_payload)
        msg = resp.choices[0].message

        # GigaChat вернул function_call → AIMessage с tool_calls
        if getattr(msg, "function_call", None):
            fc = msg.function_call
            try:
                args = json.loads(fc.arguments) if isinstance(fc.arguments, str) else (fc.arguments or {})
            except json.JSONDecodeError:
                args = {"raw": fc.arguments}

            ai_msg = AIMessage(
                content=msg.content or "",
                tool_calls=[
                    ToolCall(
                        id=f"call_{fc.name}_0",
                        name=fc.name,
                        args=args,
                    )
                ],
            )
        else:
            ai_msg = AIMessage(content=msg.content or "")

        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        import asyncio
        return await asyncio.to_thread(
            self._generate, messages, stop=stop, **kwargs
        )

    @property
    def _llm_type(self) -> str:
        return "gigachat-with-tools"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_gc_messages(messages: list[BaseMessage]) -> list:
    from gigachat.models import Messages, MessagesRole
    from gigachat.models import FunctionCall as GCFunctionCall

    result = []
    for msg in messages:
        role = _role(msg)

        if isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            result.append(Messages(
                role=MessagesRole.FUNCTION,
                content=content,
                name=getattr(msg, "name", None) or "function",
            ))
            continue

        content = msg.content if isinstance(msg.content, str) else ""

        # AIMessage с tool_calls → передаём function_call назад
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            tc = msg.tool_calls[0]
            result.append(Messages(
                role=role,
                content=content or f"Вызываю {tc['name']}",
                function_call=GCFunctionCall(
                    name=tc["name"],
                    arguments=json.dumps(tc["args"], ensure_ascii=False),
                ),
            ))
            continue

        result.append(Messages(role=role, content=content))

    return result


def _role(msg: BaseMessage):
    from gigachat.models import MessagesRole
    if isinstance(msg, SystemMessage):
        return MessagesRole.SYSTEM
    if isinstance(msg, HumanMessage):
        return MessagesRole.USER
    if isinstance(msg, AIMessage):
        return MessagesRole.ASSISTANT
    if isinstance(msg, ToolMessage):
        return MessagesRole.FUNCTION
    return MessagesRole.USER

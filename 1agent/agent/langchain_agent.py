from __future__ import annotations

import json
import os
from typing import Any, Iterator

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    HumanMessage,
    ToolMessage,
    SystemMessage,
    AIMessage,
)
from langchain_core.tools import StructuredTool
from utils.config_handler import agent_conf
from utils.logger_handler import logger


# ──────────────────────────────────────────────────────────────────────────────
# DeepSeekChat – handles ``reasoning_content`` from DeepSeek responses
# (round-tripped via ``additional_kwargs`` on the AIMessage thread, never
# via an instance attribute on the LLM client, see #12).
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_llm_kwargs() -> dict:
    """Resolve model + base_url from env > agent.yml > defaults. Defaults
    to ``deepseek-chat`` (the public DeepSeek chat model; ``deepseek-v4-flash``
    is not a public model name; see #9)."""
    cfg = (agent_conf or {}).get("llm", {}) or {}
    model = (
        os.environ.get("BI_AGENT_LLM_MODEL")
        or cfg.get("model")
        or "deepseek-chat"
    )
    base_url = (
        os.environ.get("BI_AGENT_LLM_BASE_URL")
        or cfg.get("base_url")
        or "https://api.deepseek.com/v1"
    )
    thinking_mode = (
        os.environ.get("BI_AGENT_LLM_THINKING_MODE")
        or cfg.get("thinking_mode")
        or "enable"
    )
    if model in {"deepseek-v4-flash", "deepseek-v4"}:
        logger.warning(
            f"[DeepSeekChat] model={model!r} 不是 DeepSeek 公开模型名, "
            "请改用 deepseek-chat 或 deepseek-reasoner; 旧配置继续生效仅做兼容"
        )
    return {
        "model": model,
        "base_url": base_url,
        "extra_body": {"thinking_mode": thinking_mode},
    }


class DeepSeekChat(ChatOpenAI):
    """ChatOpenAI subclass that round-trips DeepSeek's ``reasoning_content``.

    The previous implementation stored ``reasoning_content`` on the LLM
    instance itself, which races across concurrent sessions (#12). The new
    implementation only touches the AIMessage thread (``additional_kwargs``)
    and injects the last-seen reasoning_content into the next request via
    ``extra_body``.
    """

    def _create_chat_result(self, response: Any, generation_info: dict | None = None) -> Any:
        """Capture ``reasoning_content`` from the raw response and stash it
        on the resulting AIMessage's ``additional_kwargs``."""
        result = super()._create_chat_result(response, generation_info)
        try:
            raw = response.model_dump() if hasattr(response, "model_dump") else response
            choices = raw.get("choices", []) if isinstance(raw, dict) else []
            if choices and result.generations:
                rc = choices[0].get("message", {}).get("reasoning_content")
                if rc:
                    gen_msg = result.generations[0].message
                    if hasattr(gen_msg, "additional_kwargs"):
                        gen_msg.additional_kwargs["reasoning_content"] = rc
        except Exception:
            pass
        return result

    def _get_request_payload(self, input_, stop=None, **kwargs):
        """Inject the most recent ``reasoning_content`` (read from the
        last AIMessage in the thread) into ``extra_body``. We deliberately
        do NOT cache it on ``self`` to avoid cross-request races (#12)."""
        result = super()._get_request_payload(input_, stop=stop, **kwargs)
        try:
            messages = input_ if isinstance(input_, list) else list(input_ or [])
            for m in reversed(messages):
                if isinstance(m, AIMessage):
                    rc = (m.additional_kwargs or {}).get("reasoning_content")
                    if rc:
                        result.setdefault("extra_body", dict(self.extra_body or {}))
                        result["extra_body"]["reasoning_content"] = rc
                    break
        except Exception:
            pass
        return result


def create_deepseek_llm(
    api_key: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> DeepSeekChat:
    """Factory to create a DeepSeek LLM."""
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    resolved = _resolve_llm_kwargs()
    if model:
        resolved["model"] = model
    return DeepSeekChat(
        api_key=api_key,
        model=resolved["model"],
        base_url=resolved["base_url"],
        extra_body=resolved["extra_body"],
        **kwargs,
    )


# ──────────────────────────────────────────────────────────────────────────────
# LangChainToolAgent – manual tool-calling loop using LangChain @tool functions.
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_TURNS = 12
DEFAULT_MAX_HISTORY = 20


def _history_to_messages(history: list[dict]) -> list:
    """Convert a ``[{"role": ..., "content": ...}, ...]`` history into
    LangChain ``BaseMessage`` objects. Only ``user`` / ``human`` and
    ``assistant`` / ``ai`` roles are converted — tool messages and
    anything else is silently dropped, because the LLM shouldn't see
    raw tool-call envelopes from prior turns (they'd confuse the
    tool-calling loop on the next turn)."""
    out: list = []
    for entry in history or []:
        role = entry.get("role", "") if isinstance(entry, dict) else ""
        content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
        if not content:
            continue
        if role in ("user", "human"):
            out.append(HumanMessage(content=content))
        elif role in ("assistant", "ai"):
            out.append(AIMessage(content=content))
        # ignore tool / system here — system is added by the agent
    return out


class LangChainToolAgent:
    """Agent that uses LangChain @tool decorated functions with a manual
    tool-calling loop. Uses DeepSeekChat to handle reasoning_content.

    Chat history is passed in as a plain ``[{"role": ..., "content": ...}, ...]``
    list. The agent itself is **stateless** about history — the caller
    (typically ``app.py``) is responsible for storing and trimming it
    (capped at ``max_history`` by the agent as a safety net, see
    :class:`react_agent.ReactAgent`). This avoids the cross-session
    race that the prior ``self.history`` design had.
    """

    def __init__(
        self,
        tools: list[StructuredTool] | None = None,
        system_message: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_history: int = DEFAULT_MAX_HISTORY,
    ):
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.llm = create_deepseek_llm(api_key)
        self.tools = tools or []
        self.tool_map: dict[str, StructuredTool] = {t.name: t for t in self.tools}
        self.max_turns = max_turns
        self.max_history = max_history
        self.system_msg = SystemMessage(content=system_message or (
            "你是 BI 看板 SQL 引擎。"
            "用户提问时，先调用 get_table_schema 查看表结构，"
            "再调用 generate_sql 生成SQL，最后调用 execute_sql 执行。"
            "重要：如果查询成功返回数据，返回 JSON 格式："
            "{\"success\": true, \"data\": [...], \"row_count\": N}。"
        ))

    def _llm_with_tools(self):
        return self.llm.bind_tools(self.tools)

    def _format_tools(self) -> str:
        if not self.tools:
            return "无可用工具"
        return "\n".join(f"- {t.name}: {t.description or '(无描述)'}" for t in self.tools)

    def execute_stream(
        self,
        query: str,
        history: list[dict] | None = None,
    ) -> Iterator[str]:
        """Run one ReAct turn.

        ``history`` is the prior chat history (without the current user
        turn). It's prepended to the system message as LangChain
        ``BaseMessage``s so the LLM has conversational context. We
        hard-cap the number of history entries we send to
        ``self.max_history`` as a safety net (the caller should already
        cap it, but we don't trust the caller).
        """
        history = history or []
        # Defensive cap (most-recent N messages)
        history = list(history)[-self.max_history:]
        history_msgs = _history_to_messages(history)

        messages: list[Any] = [
            self.system_msg,
            *history_msgs,
            HumanMessage(content=f"可用工具:\n{self._format_tools()}\n\n用户问题: {query}"),
        ]

        # #11: enumerate so we can detect the limit from inside the loop
        # and yield the warning *immediately* (so partial progress is
        # preserved in the stream buffer instead of being clobbered).
        for turn in range(self.max_turns):
            response = self._llm_with_tools().invoke(messages)

            if response.content:
                yield response.content.strip() + "\n"

            if not response.tool_calls:
                break

            messages.append(response)

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_fn = self.tool_map.get(tool_name)
                if not tool_fn:
                    messages.append(SystemMessage(content=f"未知工具: {tool_name}"))
                    continue

                try:
                    args = json.loads(tc["args"]) if isinstance(tc["args"], str) else tc["args"]
                except Exception:
                    args = {}

                try:
                    tool_result = tool_fn.invoke(args)
                except Exception as e:
                    tool_result = f'{{"success": false, "error": "{e}"}}'

                messages.append(ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tc["id"],
                ))
        else:
            # Loop exhausted without a clean break — yield the warning
            # *now* (so the caller can surface it), but don't clobber
            # partial progress. The caller sees the warning as the last
            # chunk in the stream.
            yield f"⚠️ 工具调用轮次超过 {self.max_turns} 轮上限, 请重新开始或简化问题"


def create_agent(
    tools: list[StructuredTool] | None = None,
    system_message: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> LangChainToolAgent:
    return LangChainToolAgent(
        tools=tools,
        system_message=system_message,
        max_turns=max_turns,
        max_history=max_history,
    )

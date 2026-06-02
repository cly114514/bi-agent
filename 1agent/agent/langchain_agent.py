from __future__ import annotations

import os
import json
from typing import Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage, AIMessage
from langchain_core.tools import StructuredTool
from langchain_openai.chat_models import base as chat_base


# ──────────────────────────────────────────────────────────────────────────────
# Monkey-patch _create_chat_result to capture reasoning_content from DeepSeek.
# DeepSeek may return reasoning_content on some calls; we store it for use in
# subsequent turns. Even if we end up not needing it for deepseek-chat, keeping
# it doesn't hurt and makes the code robust for deepseek-v4-flash too.
# ──────────────────────────────────────────────────────────────────────────────
_orig_create_chat_result = chat_base.BaseChatOpenAI._create_chat_result

def _patched_create_chat_result(
    self, response: Any, generation_info: dict | None = None
) -> Any:
    result = _orig_create_chat_result(self, response, generation_info)
    try:
        raw = response.model_dump() if hasattr(response, "model_dump") else response
        choices = raw.get("choices", []) if isinstance(raw, dict) else []
        if choices:
            rc = choices[0].get("message", {}).get("reasoning_content")
            if rc and result.generations:
                gen_msg = result.generations[0].message
                if hasattr(gen_msg, "additional_kwargs"):
                    gen_msg.additional_kwargs["reasoning_content"] = rc
                self._reasoning_content = rc
    except Exception:
        pass
    return result

chat_base.BaseChatOpenAI._create_chat_result = _patched_create_chat_result


# ──────────────────────────────────────────────────────────────────────────────
# DeepSeekChat – injects reasoning_content into every request via extra_body.
# ──────────────────────────────────────────────────────────────────────────────

class DeepSeekChat(ChatOpenAI):
    """
    ChatOpenAI subclass for DeepSeek deepseek-v4-flash.

    Uses deepseek-v4-flash for tool-calling with reasoning_content handling.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("extra_body", {})
        super().__init__(**kwargs)
        self._reasoning_content: str = ""
        self._wrap_client_create()

    def _wrap_client_create(self):
        """Wrap client.with_raw_response.create to inject reasoning_content on every request."""
        orig = self.client.with_raw_response.create
        def wrapped(*args, **kwargs):
            if self._reasoning_content:
                kwargs.setdefault("extra_body", {})
                kwargs["extra_body"]["reasoning_content"] = self._reasoning_content
            return orig(*args, **kwargs)
        self.client.with_raw_response.create = wrapped

    def _get_request_payload(self, input_, stop=None, **kwargs):
        """Override to inject reasoning_content into the payload's extra_body."""
        result = super()._get_request_payload(input_, stop=stop, **kwargs)
        if self._reasoning_content:
            result.setdefault("extra_body", dict(self.extra_body))
            result["extra_body"]["reasoning_content"] = self._reasoning_content
        return result

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> Any:
        """Override to capture reasoning_content from the raw response."""
        result = super()._create_chat_result(response, generation_info)
        try:
            raw = response.model_dump() if hasattr(response, "model_dump") else response
            choices = raw.get("choices", []) if isinstance(raw, dict) else []
            if choices:
                rc = choices[0].get("message", {}).get("reasoning_content")
                if rc and result.generations:
                    gen_msg = result.generations[0].message
                    if hasattr(gen_msg, "additional_kwargs"):
                        gen_msg.additional_kwargs["reasoning_content"] = rc
                    self._reasoning_content = rc
        except Exception:
            pass
        return result


def create_deepseek_llm(
    api_key: str | None = None,
    model: str = "deepseek-v4-flash",
    **kwargs: Any,
) -> DeepSeekChat:
    """Factory to create a DeepSeek LLM."""
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    return DeepSeekChat(model=model, api_key=api_key, base_url="https://api.deepseek.com/v1", **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# LangChainToolAgent – manual tool-calling loop using LangChain @tool functions.
# ──────────────────────────────────────────────────────────────────────────────

class LangChainToolAgent:
    """
    Agent that uses LangChain @tool decorated functions with a manual
    tool-calling loop. Uses DeepSeekChat to handle reasoning_content.
    """

    def __init__(
        self,
        tools: list[StructuredTool] | None = None,
        system_message: str | None = None,
    ):
        import os
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.llm = create_deepseek_llm(api_key)
        self.tools = tools or []
        self.tool_map: dict[str, StructuredTool] = {t.name: t for t in self.tools}
        self.system_msg = SystemMessage(content=system_message or (
            "你是 BI 看板 SQL 引擎。"
            "用户提问时，先调用 get_table_schema 查看表结构，"
            "再调用 generate_sql 生成SQL，最后调用 execute_sql 执行。"
        ))

    def _llm_with_tools(self):
        return self.llm.bind_tools(self.tools)

    def _format_tools(self) -> str:
        if not self.tools:
            return "无可用工具"
        return "\n".join(f"- {t.name}: {t.description or '(无描述)'}" for t in self.tools)

    def execute_stream(self, query: str):
        messages = [
            self.system_msg,
            HumanMessage(content=f"可用工具:\n{self._format_tools()}\n\n用户问题: {query}"),
        ]

        for turn in range(12):
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
                    tool_result = json.dumps({"success": False, "error": str(e)})

                messages.append(ToolMessage(
                    content=str(tool_result),
                    tool_call_id=tc["id"],
                ))

        if turn >= 11:
            yield "会话过长，请重新开始"


def create_agent(
    tools: list[StructuredTool] | None = None,
    system_message: str | None = None,
) -> LangChainToolAgent:
    return LangChainToolAgent(tools=tools, system_message=system_message)
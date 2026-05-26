"""
手动实现 Tool Calling Agent，直接使用 OpenAI 客户端，
绕过 LangChain Agent 框架的兼容性问题。
"""
from __future__ import annotations

import os
import json
from typing import Callable, Iterable
from openai import OpenAI
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool as lc_tool, StructuredTool


def _get_openai_client() -> OpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
        timeout=60,
    )


@lc_tool
def generate_sql(query: str) -> str:
    """根据用户需求生成SQL查询语句"""
    from rag.rag_service import RagSummarizeService
    rag = RagSummarizeService()
    return rag.rag_summarize(query)


@lc_tool
def get_table_schema() -> str:
    """获取当前已上传数据表的字段结构信息"""
    from agent.tools.agent_tools import get_table_schema as _get_schema
    return _get_schema.func()


@lc_tool
def execute_sql(sql: str) -> str:
    """在MySQL数据库中执行SQL查询语句，返回JSON格式结果"""
    from agent.tools.agent_tools import execute_sql as _exec
    return _exec.func(sql)


_TOOLS = [generate_sql, get_table_schema, execute_sql]
_TOOL_NAMES = [t.name for t in _TOOLS]

_SYSTEM_PROMPT = (
    "你是 BI 看板 SQL 引擎。"
    "用户提问时，先调用 get_table_schema 查看表结构，"
    "再调用 generate_sql 生成SQL，最后调用 execute_sql 执行。"
    "重要：你的回复应该是纯文本，如果需要调用工具，在 tool_calls 中指定。"
)


def _format_tools_for_api() -> list[dict]:
    """将 LangChain tool 转为 OpenAI tool 格式"""
    result = []
    for t in _TOOLS:
        result.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        })
    return result


def _build_api_messages(history: list[dict], current_query: str) -> list[dict]:
    """构建 API 消息格式"""
    msgs = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            # Assistant message with tool calls
            assistant_msg = {"role": "assistant", "content": msg.get("content", "") or ""}
            if msg.get("tool_calls"):
                assistant_msg["tool_calls"] = msg["tool_calls"]
            msgs.append(assistant_msg)
            # Add tool results
            for tr in msg.get("tool_results", []):
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tr["id"],
                    "content": tr["content"],
                })
        else:
            msgs.append({"role": role, "content": msg.get("content", "")})
    msgs.append({"role": "user", "content": current_query})
    return msgs


def execute_stream(query: str, history: list[dict] | None = None):
    """执行 agent 流式查询"""
    if history is None:
        history = []

    client = _get_openai_client()
    messages = _build_api_messages(history, query)
    tool_map = {t.name: t for t in _TOOLS}
    last_reasoning = ""

    for turn in range(12):
        api_kwargs = {
            "model": "deepseek-v4-flash",
            "messages": messages,
            "tools": _format_tools_for_api(),
            "tool_choice": "auto",
            "extra_body": {"thinking_mode": "disable"},
        }
        if last_reasoning:
            api_kwargs["extra_body"]["reasoning_content"] = last_reasoning

        response = client.chat.completions.create(**api_kwargs)
        choice = response.choices[0]
        msg_data = choice.message

        # Collect reasoning for next turn
        reasoning = getattr(msg_data, "reasoning_content", None) or ""
        last_reasoning = reasoning

        assistant_msg = {
            "role": "assistant",
            "content": msg_data.content or "",
            "tool_calls": [],
            "tool_results": [],
        }

        if msg_data.tool_calls:
            for tc in msg_data.tool_calls:
                assistant_msg["tool_calls"].append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })

                tool_fn = tool_map.get(tc.function.name)
                if tool_fn:
                    try:
                        import json
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        raw_result = tool_fn.invoke(args)
                    except Exception as e:
                        raw_result = f'{{"success": false, "error": "{e}"}}'
                    assistant_msg["tool_results"].append({
                        "id": tc.id,
                        "content": raw_result,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": raw_result,
                    })

            if assistant_msg["tool_results"]:
                messages.append(assistant_msg)
                yield f"[调用工具] {'、'.join(tc.function.name for tc in msg_data.tool_calls)}\n"
        else:
            # No tool calls - final response
            if assistant_msg["content"]:
                yield assistant_msg["content"].strip() + "\n"
            break


class ReactAgent:
    def __init__(self):
        self._tools = _TOOLS

    def execute_stream(self, query: str):
        yield from execute_stream(query, [])
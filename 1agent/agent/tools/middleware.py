from __future__ import annotations
import logging
from typing import Any, Callable
from utils.logger_handler import logger

# Try LangChain middleware imports, fall back to simple logging if unavailable
try:
    from langchain.agents.middleware import wrap_tool_call, before_model
    from langchain.agents.middleware import AgentState, ToolCallRequest, Runtime
    from langgraph.types import Command
    _HAS_LANGCHAIN_MIDDLEWARE = True
except ImportError:
    _HAS_LANGCHAIN_MIDDLEWARE = False
    ToolCallRequest = Any
    Command = Any


if _HAS_LANGCHAIN_MIDDLEWARE:
    @wrap_tool_call
    def monitor_tool(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        logger.info(f"[tool monitor] 执行工具: {request.tool_call['name']}")
        logger.info(f"[tool monitor] 传入参数: {request.tool_call['args']}")
        try:
            result = handler(request)
            logger.info(f"[tool monitor] 工具 {request.tool_call['name']} 调用成功")
            return result
        except Exception as e:
            logger.error(f"工具 {request.tool_call['name']} 调用失败，原因: {str(e)}")
            raise e


    @before_model
    def log_before_model(state: Any, runtime: Any) -> None:
        msg_count = len(state.get("messages", [])) if isinstance(state, dict) else 0
        logger.info(f"[log_before_model] 即将调用模型，带有 {msg_count} 条消息")
else:
    # Stub when LangChain middleware unavailable
    monitor_tool = None
    log_before_model = None

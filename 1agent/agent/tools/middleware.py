from __future__ import annotations

from typing import Callable

from langchain.agents.middleware import (
    wrap_tool_call,
    before_model,
    AgentState,
    ToolCallRequest,
    Runtime,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from utils.logger_handler import logger


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:
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
def log_before_model(
    state: AgentState,
    runtime: Runtime,
) -> None:
    logger.info(f"[log_before_model] 即将调用模型，带有 {len(state['messages'])} 条消息。")

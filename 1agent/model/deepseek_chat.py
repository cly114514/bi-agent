"""
DeepSeek LLM wrapper that handles reasoning_content for deepseek-v4-flash.
For tool-calling agent use, prefer deepseek-chat which handles tool calls natively.
"""
from agent.langchain_agent import create_deepseek_llm, DeepSeekChat

__all__ = ["create_deepseek_llm", "DeepSeekChat"]
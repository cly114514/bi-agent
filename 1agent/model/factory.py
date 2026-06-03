from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional, Union

from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI

from utils.config_handler import agent_conf
from utils.logger_handler import logger


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Union[Embeddings, ChatOpenAI]]:
        pass


def _resolve_llm_config() -> dict:
    """Pick model + base_url from (env > agent.yml > defaults)."""
    cfg_llm = (agent_conf or {}).get("llm", {}) or {}
    model = (
        os.environ.get("BI_AGENT_LLM_MODEL")
        or cfg_llm.get("model")
        or "deepseek-chat"
    )
    base_url = (
        os.environ.get("BI_AGENT_LLM_BASE_URL")
        or cfg_llm.get("base_url")
        or "https://api.deepseek.com/v1"
    )
    thinking_mode = (
        os.environ.get("BI_AGENT_LLM_THINKING_MODE")
        or cfg_llm.get("thinking_mode")
        or "enable"
    )
    return {
        "model": model,
        "base_url": base_url,
        "thinking_mode": thinking_mode,
    }


class ChatModelFactory(BaseModelFactory):
    def generator(self) -> Optional[Union[Embeddings, ChatOpenAI]]:
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        )
        if not api_key:
            return None
        cfg = _resolve_llm_config()
        # Sanity-check known-bad / non-public model names (#9).
        if cfg["model"] in {"deepseek-v4-flash", "deepseek-v4"}:
            logger.warning(
                f"[factory] model={cfg['model']!r} 不是 DeepSeek 公开模型名, "
                f"请改用 deepseek-chat 或 deepseek-reasoner; 当前继续用此名只是兼容旧配置"
            )
        return ChatOpenAI(
            model=cfg["model"],
            api_key=api_key,
            base_url=cfg["base_url"],
            extra_body={"thinking_mode": cfg["thinking_mode"]},
        )


class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Optional[Union[Embeddings, ChatOpenAI]]:
        """Return a working embedding function.

        The previous implementation returned ``None``, which worked only as
        long as the persisted Chroma collection had been built with a real
        embedding function in some prior session. The first time the
        collection name changed (or chroma_db was rebuilt) the agent
        crashed (#2).

        Resolution order:
          1. ``EMBEDDING_MODEL`` env var (any class supported by
             langchain_community.embeddings) — full control.
          2. ``agent.yml -> embeddings.model`` — repo config.
          3. HuggingFace ``shibing624/text2vec-base-chinese`` — small
             Chinese-friendly default.
          4. ``None`` — log a loud warning and let the caller fail-fast.
        """
        from utils.config_handler import agent_conf
        cfg_emb = (agent_conf or {}).get("embeddings", {}) or {}
        chosen = (
            os.environ.get("BI_AGENT_EMBED_MODEL")
            or cfg_emb.get("model")
            or "shibing624/text2vec-base-chinese"
        )
        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            return HuggingFaceEmbeddings(model_name=chosen)
        except ImportError:
            logger.warning(
                f"[factory] langchain_community.embeddings 不可用, "
                f"无法加载 embedding 模型 {chosen!r}. "
                f"请 pip install langchain-community sentence-transformers"
            )
            return None
        except Exception as e:
            logger.warning(f"[factory] 加载 embedding 模型 {chosen!r} 失败: {e}")
            return None


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()

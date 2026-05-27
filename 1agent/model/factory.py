from abc import ABC, abstractmethod
from typing import Optional, Union
from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI
from utils.config_handler import rag_conf


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Union[Embeddings, ChatOpenAI]]:
        pass


class ChatModelFactory(BaseModelFactory):
    def generator(self) -> Optional[Union[Embeddings, ChatOpenAI]]:
        import os
        api_key = (
            os.environ.get("OPENAI_API_KEY") or
            os.environ.get("DEEPSEEK_API_KEY") or
            os.environ.get("DASHSCOPE_API_KEY") or
            ""
        )
        if not api_key:
            return None
        return ChatOpenAI(
            model="deepseek-v4-flash",
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            extra_body={"thinking_mode": "disable"},
        )


class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Optional[Union[Embeddings, ChatOpenAI]]:
        # DeepSeek embeddings not yet supported; use a stub
        return None


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()
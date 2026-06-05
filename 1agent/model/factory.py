from abc import ABC, abstractmethod
from typing import Optional, Union
from langchain_core.embeddings import Embeddings
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from utils.config_handler import rag_conf
import os


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Union[Embeddings, BaseChatModel]]:
        pass


class ChatModelFactory(BaseModelFactory):
    def generator(self) -> Optional[Union[Embeddings, BaseChatModel]]:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        return ChatOpenAI(
            model=rag_conf["chat_model_name"],
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
        )


class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Optional[Union[Embeddings, BaseChatModel]]:
        return DashScopeEmbeddings(model=rag_conf["embedding_model_name"])


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()

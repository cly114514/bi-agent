
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""
RAG SQL生成服务：用户提问 → 向量检索相似SQL示例 → 模型生成SQL语句
"""
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from rag.vector_store import VectorStoreService
from utils.prompt_loader import load_rag_prompts
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model
class RagSummarizeService(object):
    def __init__(self):
        self.vector_store = VectorStoreService()
        self.retriever = self.vector_store.get_retriever()
        self.prompt_text = load_rag_prompts()
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)
        self.model = chat_model
        self.chain = self._init_chain()

    def _init_chain(self):
        if self.model is None:
            return None
        chain = self.prompt_template | self.model | StrOutputParser()
        return chain

    def retriever_docs(self, query: str) -> list[Document]:
        return self.retriever.invoke(query)

    def rag_summarize(self, query: str) -> str:
        if self.chain is None:
            return "SELECT '未找到匹配的SQL示例，请补充数据源信息' AS result"
        context_docs = self.retriever_docs(query)
        if not context_docs:
            return "SELECT '未找到匹配的SQL示例，请补充数据源信息' AS result"

        context = ""
        counter = 0
        for doc in context_docs:
            counter += 1
            sql = doc.metadata.get("sql", "")
            context += f"【参考示例{counter}】描述：{doc.page_content}\n对应的SQL：{sql}\n\n"

        return self.chain.invoke({
            "input": query,
            "context": context,
        })


if __name__ == '__main__':
    vs = VectorStoreService()
    vs.load_document()
    rag = RagSummarizeService()
    result = rag.rag_summarize("查询华东地区销售额")
    print("=== 检索到的文档 ===")
    for doc in rag.retriever_docs("查询华东地区销售额"):
        print(f"  {doc.page_content} -> {doc.metadata.get('sql', '')[:80]}...")
    print("\n=== 生成的SQL ===")
    print(result)

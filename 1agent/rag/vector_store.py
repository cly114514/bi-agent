import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_chroma import Chroma
from langchain_core.documents import Document
from utils.config_handler import chroma_conf
from model.factory import embed_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
from utils.file_handler import pdf_loader, txt_loader, listdir_with_allowed_type, get_file_md5_hex
from utils.logger_handler import logger


def _is_sql_examples_file(filepath: str) -> bool:
    """Detect the JSONL ``{"description": ..., "sql": ...}`` examples file.

    #26: previously detected by colon-line ratio. New format is JSONL with
    a leading ``{`` on every line; we sniff the first non-empty line.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    return s.startswith("{") and '"sql"' in s
        return False
    except Exception:
        return False


def _load_sql_knowledge(filepath: str) -> list[Document]:
    """Load a JSONL ``{"description": ..., "sql": ...}`` examples file.

    #26: replaced the legacy ``split(":", 1)`` parser, which broke for any
    SQL containing a ``:`` character. JSONL is unambiguous.
    """
    docs: list[Document] = []
    filename = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"[vector_store] {filename}:{idx} JSON 解析失败, 跳过: {e}")
                continue
            description = str(obj.get("description", "")).strip()
            sql = str(obj.get("sql", "")).strip()
            if not description or not sql:
                logger.warning(f"[vector_store] {filename}:{idx} description/sql 为空, 跳过")
                continue
            docs.append(Document(
                page_content=description,
                metadata={
                    "source": f"{filename}_line_{idx}",
                    "sql": sql,
                    "file": filename,
                },
            ))
    return docs


class VectorStoreService:
    def __init__(self):
        self.vector_store = Chroma(
            collection_name=chroma_conf["collection_name"],
            embedding_function=embed_model,
            persist_directory=chroma_conf["persist_directory"],
        )

        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separators"],
            length_function=len,
        )

    def get_retriever(self):
        return self.vector_store.as_retriever(search_kwargs={"k": chroma_conf["k"]})

    def load_document(self):
        """
        从数据文件夹内读取数据文件，转为向量存入向量库。
        自动识别 描述:SQL 格式的 txt 文件，按行拆分为独立 Document，
        description 用于向量检索，SQL 存入 metadata。
        :return: None
        """

        def check_md5_hex(md5_for_check: str):
            if not os.path.exists(get_abs_path(chroma_conf["md5_hex_store"])):
                open(get_abs_path(chroma_conf["md5_hex_store"]), "w", encoding="utf-8").close()
                return False

            with open(get_abs_path(chroma_conf["md5_hex_store"]), "r", encoding="utf-8") as f:
                for line in f.readlines():
                    line = line.strip()
                    if line == md5_for_check:
                        return True
                return False

        def save_md5_hex(md5_for_check: str):
            with open(get_abs_path(chroma_conf["md5_hex_store"]), "a", encoding="utf-8") as f:
                f.write(md5_for_check + "\n")

        def get_file_documents(read_path: str):
            if _is_sql_examples_file(read_path):
                return _load_sql_knowledge(read_path)
            if read_path.endswith("pdf"):
                return pdf_loader(read_path)
            if read_path.endswith("txt"):
                return txt_loader(read_path)
            return []

        allowed_files_path: list[str] = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"]),
        )

        for path in allowed_files_path:
            md5_hex = get_file_md5_hex(path)

            if check_md5_hex(md5_hex):
                logger.info(f"[加载知识库]{path}内容已经存在知识库内，跳过")
                continue

            try:
                documents: list[Document] = get_file_documents(path)

                if not documents:
                    logger.warning(f"[加载知识库]{path}内没有有效文本内容，跳过")
                    continue

                is_sql_file = _is_sql_examples_file(path)
                if is_sql_file:
                    self.vector_store.add_documents(documents)
                else:
                    split_document: list[Document] = self.spliter.split_documents(documents)
                    if not split_document:
                        logger.warning(f"[加载知识库]{path}分片后没有有效文本内容，跳过")
                        continue
                    self.vector_store.add_documents(split_document)

                save_md5_hex(md5_hex)

                logger.info(f"[加载知识库]{path} 内容加载成功")
            except Exception as e:
                logger.error(f"[加载知识库]{path}加载失败：{str(e)}", exc_info=True)
                continue


if __name__ == '__main__':
    vs = VectorStoreService()

    vs.load_document()

    retriever = vs.get_retriever()

    res = retriever.invoke("迷路")
    for r in res:
        print(r.page_content)
        print("-"*20)



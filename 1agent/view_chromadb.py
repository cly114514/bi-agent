import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.config_handler import chroma_conf
import chromadb

if __name__ == "__main__":
    client = chromadb.PersistentClient(path=chroma_conf["persist_directory"])
    collection = client.get_or_create_collection(chroma_conf["collection_name"])

    total = collection.count()
    print(f"Collection: {chroma_conf['collection_name']}")
    print(f"Persist dir: {chroma_conf['persist_directory']}")
    print(f"Total records: {total}\n")

    results = collection.get(limit=min(total, 200))
    if results.get("documents"):
        for i, (doc, meta) in enumerate(
            zip(results["documents"], results["metadatas"])
        ):
            sql = meta.get("sql", "(无)")
            source = meta.get("source", "")
            print(f"--- 记录 {i + 1} ---")
            print(f"  描述(检索用): {doc}")
            print(f"  SQL: {sql}")
            print(f"  来源: {source}")
            print()
    else:
        print("ChromaDB 为空，尚未加载数据。请先运行 vector_store.load_document()。")

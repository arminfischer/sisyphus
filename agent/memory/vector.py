from __future__ import annotations

import logging
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from agent.config import settings

log = logging.getLogger("sisyphus.vector")

_client: chromadb.PersistentClient | None = None
_collection: Any = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        _collection = _client.get_or_create_collection(
            name="sisyphus_memory",
            metadata={"hnsw:space": "cosine"},
        )
        log.info(
            f"ChromaDB initialised at {settings.chroma_path} "
            f"({_collection.count()} existing documents)"
        )
    return _collection


def add_document(doc_id: str, text: str, metadata: dict | None = None) -> None:
    """Insert or update a document in the vector store."""
    col = _get_collection()
    col.upsert(ids=[doc_id], documents=[text], metadatas=[metadata or {}])


def search(query: str, n_results: int = 5) -> list[dict]:
    """Return top-n semantically similar documents."""
    col = _get_collection()
    count = col.count()
    if count == 0:
        return []
    actual_n = min(n_results, count)
    results = col.query(query_texts=[query], n_results=actual_n)
    output = []
    for i, doc in enumerate(results["documents"][0]):
        output.append(
            {
                "text": doc,
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            }
        )
    return output


def collection_count() -> int:
    try:
        return _get_collection().count()
    except Exception:
        return 0

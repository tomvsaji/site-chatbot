from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

from rag import BM25Index, Chunk, tokenize


QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "site_chunks")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_CACHE_DIR = os.getenv(
    "EMBEDDING_CACHE_DIR", "/app/.cache/fastembed"
)
MIN_SEMANTIC_SCORE = float(os.getenv("MIN_SEMANTIC_SCORE", "0.22"))
MIN_LEXICAL_SCORE = float(os.getenv("MIN_LEXICAL_SCORE", "2.0"))


@dataclass(frozen=True)
class RetrievalMatch:
    chunk: Chunk
    score: float
    semantic_score: float = 0.0
    lexical_score: float = 0.0


class HybridIndex:
    """Persist chunk embeddings in Qdrant and combine vector and BM25 rankings."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Index(chunks)
        self.embedder = TextEmbedding(
            model_name=EMBEDDING_MODEL,
            cache_dir=EMBEDDING_CACHE_DIR,
        )
        self.client = QdrantClient(url=QDRANT_URL, timeout=30)
        self._wait_for_qdrant()
        self._rebuild_vector_index()

    def _wait_for_qdrant(self, attempts: int = 30) -> None:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                self.client.get_collections()
                return
            except Exception as error:
                last_error = error
                time.sleep(2)
        raise RuntimeError(f"Qdrant is unavailable at {QDRANT_URL}") from last_error

    def _rebuild_vector_index(self) -> None:
        vectors = list(self.embedder.embed([chunk.text for chunk in self.chunks]))
        vector_size = len(vectors[0]) if vectors else 384

        if self.client.collection_exists(QDRANT_COLLECTION):
            self.client.delete_collection(QDRANT_COLLECTION)
        self.client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

        if not vectors:
            return

        points = [
            models.PointStruct(
                id=position,
                vector=vector.tolist(),
                payload={
                    "source": chunk.source,
                    "text": chunk.text,
                    "title": chunk.title,
                    "heading": chunk.heading,
                    "url": chunk.url,
                    "chunk_index": position,
                    "embedding_model": EMBEDDING_MODEL,
                },
            )
            for position, (chunk, vector) in enumerate(zip(self.chunks, vectors))
        ]
        self.client.upsert(
            collection_name=QDRANT_COLLECTION,
            points=points,
            wait=True,
        )

    def _semantic_search(self, query: str, limit: int) -> list[tuple[Chunk, float]]:
        query_vector = next(self.embedder.query_embed(query)).tolist()
        result = self.client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            with_payload=True,
        )
        matches: list[tuple[Chunk, float]] = []
        for point in result.points:
            payload = point.payload or {}
            text = str(payload.get("text", ""))
            source = str(payload.get("source", "unknown"))
            if text:
                matches.append(
                    (
                        Chunk(
                            source=source,
                            text=text,
                            tokens=tokenize(text),
                            title=str(payload.get("title", "")),
                            heading=str(payload.get("heading", "")),
                            url=str(payload.get("url", "")),
                        ),
                        float(point.score),
                    )
                )
        return matches

    def search(self, query: str, limit: int = 5) -> list[RetrievalMatch]:
        """Fuse semantic and lexical ranks using reciprocal-rank fusion."""
        candidate_limit = max(limit * 3, 10)
        semantic = self._semantic_search(query, candidate_limit)
        lexical = self.bm25.search(query, candidate_limit)

        chunks: dict[tuple[str, str], Chunk] = {}
        fused_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
        semantic_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
        lexical_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
        for results in (semantic, lexical):
            for rank, (chunk, raw_score) in enumerate(results, start=1):
                key = (chunk.source, chunk.text)
                chunks[key] = chunk
                fused_scores[key] += 1.0 / (60 + rank)
                if results is semantic:
                    semantic_scores[key] = max(semantic_scores[key], raw_score)
                else:
                    lexical_scores[key] = max(lexical_scores[key], raw_score)

        ranked = sorted(
            fused_scores.items(),
            key=lambda item: (
                item[1] + (0.0004 if item[0][0] == "tom-facts.md" else 0.0),
                semantic_scores[item[0]],
            ),
            reverse=True,
        )
        matches: list[RetrievalMatch] = []
        source_counts: defaultdict[str, int] = defaultdict(int)
        for key, score in ranked:
            chunk = chunks[key]
            if source_counts[chunk.source] >= 2:
                continue
            source_counts[chunk.source] += 1
            matches.append(
                RetrievalMatch(
                    chunk=chunk,
                    score=score,
                    semantic_score=semantic_scores[key],
                    lexical_score=lexical_scores[key],
                )
            )
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def is_relevant(matches: list[RetrievalMatch]) -> bool:
        if not matches:
            return False
        return any(
            match.semantic_score >= MIN_SEMANTIC_SCORE
            or match.lexical_score >= MIN_LEXICAL_SCORE
            for match in matches[:2]
        )

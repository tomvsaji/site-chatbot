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
PROFILE_SOURCES = frozenset({'tom-facts.md', 'site-home.md', 'site-about.md'})


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
        vectors = list(self.embedder.embed([chunk.text for chunk in self.chunks], batch_size=16))
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

    def _semantic_search(
        self, query: str, limit: int, source: str | None = None, sources: set[str] | None = None
    ) -> list[tuple[Chunk, float]]:
        if sources is not None and not sources:
            return []
        conditions = []
        if source:
            conditions.append(models.FieldCondition(key='source', match=models.MatchValue(value=source)))
        if sources is not None:
            conditions.append(models.FieldCondition(key='source', match=models.MatchAny(any=sorted(sources))))
        query_vector = next(self.embedder.query_embed(query)).tolist()
        result = self.client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            query_filter=models.Filter(must=conditions) if conditions else None,
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

    def search(
        self, query: str, limit: int = 5, source: str | None = None, source_type: str = 'all'
    ) -> list[RetrievalMatch]:
        """Fuse semantic and lexical ranks using reciprocal-rank fusion."""
        if source_type not in {'profile','writing','all'}:
            raise ValueError('Unknown document source type.')
        # Document metadata, chosen by the model; never inspect query keywords.
        available_sources = {chunk.source for chunk in self.chunks}
        if source_type == 'profile':
            sources = available_sources.intersection(PROFILE_SOURCES)
        elif source_type == 'writing':
            sources = {name for name in available_sources if name == 'site-writing.md' or name.startswith('posts/')}
        else:
            sources = None
        candidate_limit = max(limit * 3, 10)
        semantic = self._semantic_search(query, candidate_limit, source=source, sources=sources)
        lexical_index = (
            BM25Index([chunk for chunk in self.chunks if (not source or chunk.source == source) and (sources is None or chunk.source in sources)])
            if source or sources is not None
            else self.bm25
        )
        lexical = lexical_index.search(query, candidate_limit)

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
                item[1],
                semantic_scores[item[0]],
            ),
            reverse=True,
        )
        matches: list[RetrievalMatch] = []
        source_counts: defaultdict[str, int] = defaultdict(int)
        for key, score in ranked:
            chunk = chunks[key]
            if source and chunk.source != source:
                continue
            if sources is not None and chunk.source not in sources:
                continue
            if not source and source_counts[chunk.source] >= 4:
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

    def chunks_for_source(
        self, source: str, limit: int = 4
    ) -> list[RetrievalMatch]:
        """Return deterministic source chunks for explicit catalog requests."""
        return [
            RetrievalMatch(
                chunk=chunk,
                score=1.0,
                semantic_score=1.0,
                lexical_score=1.0,
            )
            for chunk in self.chunks
            if chunk.source == source
        ][:limit]

    def chunks_for_sections(
        self, source: str, section_markers: list[str]
    ) -> list[RetrievalMatch]:
        """Return one representative chunk for each requested document section."""
        selected: list[RetrievalMatch] = []
        for marker in section_markers:
            chunk = next(
                (
                    candidate
                    for candidate in self.chunks
                    if candidate.source == source
                    and marker.lower() in candidate.heading.lower()
                ),
                None,
            )
            if chunk is not None:
                selected.append(
                    RetrievalMatch(
                        chunk=chunk,
                        score=1.0,
                        semantic_score=1.0,
                        lexical_score=1.0,
                    )
                )
        return selected

    @staticmethod
    def is_relevant(matches: list[RetrievalMatch]) -> bool:
        if not matches:
            return False
        return any(
            match.semantic_score >= MIN_SEMANTIC_SCORE
            or match.lexical_score >= MIN_LEXICAL_SCORE
            for match in matches[:2]
        )

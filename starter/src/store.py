"""PostgreSQL + pgvector store: idempotent upsert, cosine search, metadata filter.

One table holds all strategies (column `strategy`). Search is exact (no ANN
index): the corpus is small and exact search guarantees that SQL metadata
filters never shrink the top-k the way post-filtered HNSW can.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from .models import Chunk

ALLOWED_FILTER_KEYS = {"chapter", "section", "document_type", "language"}


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be > 0")
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.database_url, connect_timeout=5)
        register_vector(conn)
        return conn

    def create_schema(self) -> None:
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id    TEXT PRIMARY KEY,
                    strategy    TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    page        INT  NOT NULL,
                    text        TEXT NOT NULL,
                    metadata    JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding   vector({self.dimensions}) NOT NULL,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_strategy_idx ON chunks (strategy)")
            # Keyword side of hybrid retrieval: English full-text vector, generated from
            # the same chunk text (existing rows are filled automatically).
            conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector "
                         "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv)")
            row = conn.execute(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass "
                "AND attname = 'embedding'").fetchone()
            if row and row[0] != self.dimensions:
                raise ValueError(f"Table chunks has vector({row[0]}) but model has "
                                 f"{self.dimensions} dimensions; use a fresh table.")

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> dict:
        """Insert/update by stable chunk_id and delete chunks of the same
        (strategy, document) that are no longer produced (changed documents)."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length differ")
        if any(len(v) != self.dimensions for v in vectors):
            raise ValueError("vector dimension mismatch")
        ids_by_doc: dict[str, list[str]] = {}
        for c in chunks:
            ids_by_doc.setdefault(c.document_id, []).append(c.chunk_id)
        deleted = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO chunks (chunk_id, strategy, document_id, source, page, text,
                                           metadata, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                       ON CONFLICT (chunk_id) DO UPDATE SET
                         text = EXCLUDED.text, metadata = EXCLUDED.metadata,
                         embedding = EXCLUDED.embedding, ingested_at = now()""",
                    [(c.chunk_id, strategy, c.document_id, c.source, c.page, c.text,
                      json.dumps(c.metadata, ensure_ascii=False), np.asarray(v, dtype=np.float32))
                     for c, v in zip(chunks, vectors)])
                for doc_id, ids in ids_by_doc.items():
                    cur.execute("DELETE FROM chunks WHERE strategy = %s AND document_id = %s "
                                "AND NOT (chunk_id = ANY(%s))", (strategy, doc_id, ids))
                    deleted += cur.rowcount
            conn.commit()
        return {"upserted": len(chunks), "deleted_obsolete": deleted}

    def count(self, strategy: str) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT count(*) FROM chunks WHERE strategy = %s",
                                (strategy,)).fetchone()[0]

    @staticmethod
    def _where(strategy: str, filters: dict[str, Any] | None) -> tuple[list[str], list[Any]]:
        where, params = ["strategy = %s"], [strategy]
        for key, value in (filters or {}).items():
            if key not in ALLOWED_FILTER_KEYS:
                raise ValueError(f"filter key not allowed: {key}")
            where.append("metadata ->> %s = %s")
            params += [key, str(value)]
        return where, params

    def keyword_search(self, strategy: str, query: str, top_k: int = 20,
                       filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Full-text (BM25-like) ranking with PostgreSQL ts_rank_cd.

        The question is turned into English lexemes (stop words removed, stemmed)
        and OR-ed together, so a chunk matches if it shares any content word;
        ts_rank_cd ranks by term frequency and proximity. Same SQL filters as search().
        """
        where, params = self._where(strategy, filters)
        sql = ("WITH q AS (SELECT to_tsquery('simple', coalesce(array_to_string(ARRAY("
               "SELECT quote_literal(l) FROM unnest(tsvector_to_array(to_tsvector('english', %s))) l"
               "), ' | '), '')) AS query) "
               "SELECT chunk_id, document_id, source, page, text, metadata, "
               "ts_rank_cd(tsv, q.query) AS score FROM chunks, q "
               f"WHERE {' AND '.join(where)} AND tsv @@ q.query "
               "ORDER BY score DESC, chunk_id LIMIT %s")
        with self._connect() as conn:
            rows = conn.execute(sql, [query, *params, top_k]).fetchall()
        keys = ["chunk_id", "document_id", "source", "page", "text", "metadata", "score"]
        return [dict(zip(keys, r)) for r in rows]

    def hybrid_search(self, strategy: str, query: str, query_vector: list[float],
                      top_k: int = 3, filters: dict[str, Any] | None = None,
                      k: int = 60, w_dense: float = 1.0, w_keyword: float = 1.0,
                      depth: int = 20) -> list[dict[str, Any]]:
        """Weighted Reciprocal Rank Fusion of dense and keyword rankings:
        score = w_dense / (k + rank_dense) + w_keyword / (k + rank_keyword)."""
        dense = self.search(strategy, query_vector, depth, filters)
        keyword = self.keyword_search(strategy, query, depth, filters)
        fused: dict[str, dict[str, Any]] = {}
        for name, hits, weight in (("dense", dense, w_dense), ("keyword", keyword, w_keyword)):
            for rank, h in enumerate(hits, start=1):
                item = fused.setdefault(h["chunk_id"], {**h, "score": 0.0, "ranks": {}})
                item["score"] += weight / (k + rank)
                item["ranks"][name] = rank
        ordered = sorted(fused.values(), key=lambda h: (-h["score"], h["chunk_id"]))
        return ordered[:top_k]

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Cosine top-k within one strategy; filters are exact matches on
        whitelisted metadata keys, applied in SQL before ranking."""
        qv = np.asarray(query_vector, dtype=np.float32)
        where, params = self._where(strategy, filters)
        sql = (f"SELECT chunk_id, document_id, source, page, text, metadata, "
               f"1 - (embedding <=> %s) AS score FROM chunks WHERE {' AND '.join(where)} "
               f"ORDER BY embedding <=> %s LIMIT %s")
        with self._connect() as conn:
            rows = conn.execute(sql, [qv, *params, qv, top_k]).fetchall()
        keys = ["chunk_id", "document_id", "source", "page", "text", "metadata", "score"]
        return [dict(zip(keys, r)) for r in rows]

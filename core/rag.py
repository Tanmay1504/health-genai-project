"""
core/rag.py — Retrieval-Augmented Generation plumbing (Chroma + embeddings).

Two collections are built from the hand-written corpus in data/knowledge/:

    policies   <- data/knowledge/policies/*.md   (fictional insurer policy wording)
    clinical   <- data/knowledge/clinical/*.md   (illustrative clinical guidance)

They are separate because the appeal agent searches them for different jobs:
  * policy  -> "what does the contract say?"           (the legal argument)
  * clinical-> "why was this treatment necessary?"     (the medical argument)
Keeping them apart stops a clinical paragraph from crowding out the one policy
clause that actually wins the appeal.

Chunking strategy (WHY it matters)
  Policy documents are numbered clauses ("4.2 Pre-existing diseases ...").
  A citation is only useful if one chunk is one clause, so we first split on
  markdown headers (each clause gets its own header), and only then apply a
  character splitter as a safety net for very long clauses. Each chunk keeps its
  header path as metadata, so a citation can say *which clause* it came from.

Run:  python -m core.rag --rebuild      (re-embeds everything)
      python -m core.rag --query "cataract sub-limit" --plan SGH-SILVER
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from core.config import PATHS
from core.llm import get_embeddings

COLLECTIONS = ("policies", "clinical")


@dataclass
class RetrievedChunk:
    """What every retrieval returns. `chunk_id` is stable across rebuilds, so a
    citation stored in the audit log can be traced back to the exact text."""

    chunk_id: str
    collection: str
    source: str
    section: str
    text: str
    score: float  # cosine distance: lower = more similar

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# -----------------------------------------------------------------------------
# Loading + chunking
# -----------------------------------------------------------------------------
def _parse_front_matter(raw: str) -> tuple[dict, str]:
    """Tiny YAML-ish front matter parser: lines `key: value` between --- fences.
    We avoid a YAML dependency for 3 keys."""
    meta: dict[str, str] = {}
    m = re.match(r"^---\n(.*?)\n---\n", raw, flags=re.S)
    if not m:
        return meta, raw
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, raw[m.end():]


def load_chunks(collection: str) -> list[Document]:
    folder = PATHS.knowledge / collection
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")], strip_headers=False
    )
    # Safety net for unusually long sections; 1200 chars ≈ 250-300 tokens.
    char_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)

    docs: list[Document] = []
    for path in sorted(folder.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        sections = header_splitter.split_text(body)
        for sec in char_splitter.split_documents(sections):
            section_path = " > ".join(sec.metadata[h] for h in ("h1", "h2", "h3") if h in sec.metadata)
            docs.append(
                Document(
                    page_content=sec.page_content,
                    metadata={
                        "source": path.name,
                        "section": section_path,
                        "title": meta.get("title", path.stem),
                        # Chroma metadata must be scalar; "ALL" = applies to every plan.
                        "plan_code": meta.get("plan_code", "ALL"),
                        "collection": collection,
                    },
                )
            )
    # Stable ids: <file-stem>#<n>. Used as citation keys downstream.
    counters: dict[str, int] = {}
    for d in docs:
        stem = Path(d.metadata["source"]).stem
        counters[stem] = counters.get(stem, 0) + 1
        d.metadata["chunk_id"] = f"{stem}#{counters[stem]}"
    return docs


# -----------------------------------------------------------------------------
# Vector store
# -----------------------------------------------------------------------------
def _collection_name(collection: str, embed_name: str) -> str:
    # Chroma names: 3-63 chars of [A-Za-z0-9._-]. Namespacing by embedder means
    # switching LLM_PROVIDER never mixes incompatible vectors.
    return re.sub(r"[^A-Za-z0-9._-]", "-", f"{collection}__{embed_name}")[:63]


@lru_cache(maxsize=4)
def get_store(collection: str) -> Chroma:
    embeddings, embed_name = get_embeddings()
    return Chroma(
        collection_name=_collection_name(collection, embed_name),
        embedding_function=embeddings,
        persist_directory=str(PATHS.chroma),
        collection_metadata={"hnsw:space": "cosine"},
    )


def build_index(rebuild: bool = True) -> dict[str, int]:
    """(Re)embed the knowledge corpus. Returns chunk counts per collection."""
    PATHS.chroma.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name in COLLECTIONS:
        store = get_store(name)
        if rebuild:
            existing = store.get(include=[])["ids"]
            if existing:
                store.delete(ids=existing)
        docs = load_chunks(name)
        store.add_documents(docs, ids=[d.metadata["chunk_id"] for d in docs])
        counts[name] = len(docs)
    return counts


class RAGUnavailable(RuntimeError):
    """Raised when the embedding backend can't be reached (e.g. Ollama not running)."""


def index_is_empty() -> bool:
    return any(len(get_store(c).get(include=[], limit=1)["ids"]) == 0 for c in COLLECTIONS)


def retrieve(query: str, collection: str, k: int = 4, plan_code: str | None = None) -> list[RetrievedChunk]:
    """Top-k chunks for `query`. For policies, `plan_code` restricts results to
    that plan's wording plus documents marked ALL (claims procedure etc.), so an
    appeal for a Silver policy never cites a Gold clause."""
    try:
        if index_is_empty():
            build_index()
    except Exception as exc:  # most often: Ollama not running / model not pulled / bad API key
        raise RAGUnavailable(f"Embedding backend unavailable for RAG ({exc.__class__.__name__}: {exc}). "
                             "Start Ollama (`ollama serve`) and pull the embed model, check OPENAI_API_KEY, "
                             "or set LLM_PROVIDER=mock.") from exc
    where = None
    if collection == "policies" and plan_code:
        where = {"plan_code": {"$in": [plan_code, "ALL"]}}
    hits = get_store(collection).similarity_search_with_score(query, k=k, filter=where)
    return [
        RetrievedChunk(
            chunk_id=d.metadata["chunk_id"],
            collection=collection,
            source=d.metadata["source"],
            section=d.metadata.get("section", ""),
            text=d.page_content,
            score=round(float(score), 4),
        )
        for d, score in hits
    ]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build or query the RAG index.")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--collection", default="policies", choices=COLLECTIONS)
    ap.add_argument("--plan")
    args = ap.parse_args()
    if args.rebuild:
        print("Indexed chunks:", build_index())
    if args.query:
        for c in retrieve(args.query, args.collection, plan_code=args.plan):
            print(f"[{c.chunk_id}] ({c.score}) {c.section}\n  {c.text[:160]!r}\n")

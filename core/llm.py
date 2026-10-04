"""
core/llm.py — the ONE interface every agent uses to talk to a language model.

Design
------
Agents never import `langchain_openai` or `langchain_ollama` directly. They call

    llm = get_llm()
    text = llm.complete(system="...", user="...")
    data = llm.complete_json(system="...", user="...", default={...})

and `get_llm()` decides, from the LLM_PROVIDER env var, which backend to build:

    LLM_PROVIDER=openai -> langchain_openai.ChatOpenAI  (needs OPENAI_API_KEY)
    LLM_PROVIDER=groq   -> ChatOpenAI pointed at Groq's OpenAI-compatible
                           endpoint (needs GROQ_API_KEY; very fast hosted open models)
    LLM_PROVIDER=ollama -> langchain_ollama.ChatOllama   (local, no key)
    LLM_PROVIDER=mock   -> no model; returns "" so agents fall back to their
                           deterministic templates (offline dev + unit tests)

WHY wrap LangChain at all, when it already abstracts providers?
  1. Swapping providers must be a config change, not a code change.
  2. The audit trail needs to know *which* provider/model produced a decision —
     `LLMClient.describe()` gives that in one call.
  3. Local 8B models often wrap JSON in prose or ``` fences. Putting robust JSON
     extraction here means every agent benefits and none re-implements it.
  4. If the model is down, `complete()` returns "" instead of crashing the
     claim pipeline; agents then take their template path, and the audit
     record shows that no LLM output was used.

Embeddings get the same treatment through `get_embeddings()`, selected by
EMBEDDINGS_PROVIDER (defaults to the chat provider; Groq has no embeddings
API, so groq defaults to "local": FastEmbed running a small model on the CPU).
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

import numpy as np
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import settings

log = logging.getLogger(__name__)


class LLMClient:
    """Thin provider-agnostic wrapper around a LangChain chat model."""

    def __init__(self, provider: str, model_name: str, chat_model: Any | None):
        self.provider = provider
        self.model_name = model_name
        self._chat = chat_model  # None for the mock provider

    # -- metadata for the audit log ---------------------------------------------
    def describe(self) -> dict[str, str]:
        return {"llm_provider": self.provider, "llm_model": self.model_name}

    @property
    def is_mock(self) -> bool:
        return self._chat is None

    # -- calls ----------------------------------------------------------------
    def complete(self, system: str, user: str) -> str:
        """Plain text completion. Returns "" on mock provider or on any error."""
        if self._chat is None:
            return ""
        try:
            resp = self._chat.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            text = (resp.content or "").strip() if isinstance(resp.content, str) else str(resp.content)
            return normalize_citations(text)
        except Exception as exc:  # network down, model not pulled, quota, ...
            log.warning("LLM call failed (%s/%s): %s", self.provider, self.model_name, exc)
            return ""

    def complete_json(self, system: str, user: str, default: dict | list) -> Any:
        """Ask for JSON; return the parsed object, or `default` if parsing fails.

        Callers always pass a sensible `default` so a bad generation degrades
        the output instead of crashing the pipeline.
        """
        raw = self.complete(system + "\nRespond with valid JSON only. No prose, no markdown fences.", user)
        parsed = extract_json(raw)
        if parsed is None or (isinstance(default, dict) and not isinstance(parsed, dict)) \
                or (isinstance(default, list) and not isinstance(parsed, list)):
            return default
        return parsed


# Some models (e.g. gpt-oss) cite as 【source#3】 or 【source#3†L1-L4】 instead of
# [source#3]. Agents verify citations with a [..] regex, so normalise here once.
_FULLWIDTH_CITE = re.compile(r"【([^】†]+)(?:†[^】]*)?】")


def normalize_citations(text: str) -> str:
    return _FULLWIDTH_CITE.sub(lambda m: f"[{m.group(1).strip()}]", text)


def extract_json(text: str) -> Any | None:
    """Pull the first JSON object/array out of an LLM reply.

    Handles the usual messes: ```json fences, a sentence before the JSON, etc.
    """
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} or [...] span.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


@lru_cache(maxsize=1)
def get_llm() -> LLMClient:
    """Build (once per process) the chat model selected by LLM_PROVIDER."""
    provider = settings.llm_provider
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is empty. Set it in .env.")
        from langchain_openai import ChatOpenAI  # imported lazily: only needed for this provider

        chat = ChatOpenAI(
            model=settings.openai_model,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key,
            timeout=120,
        )
        return LLMClient("openai", settings.openai_model, chat)

    if provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError("LLM_PROVIDER=groq but GROQ_API_KEY is empty. Set it in .env.")
        from langchain_openai import ChatOpenAI

        # Groq speaks the OpenAI wire protocol, so the OpenAI client works with a
        # different base_url: no extra dependency. Extra retries absorb the
        # free tier's rate limits (HTTP 429) with exponential backoff.
        chat = ChatOpenAI(
            model=settings.groq_model,
            temperature=settings.llm_temperature,
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            timeout=120,
            max_retries=6,
        )
        return LLMClient("groq", settings.groq_model, chat)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        chat = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=settings.llm_temperature,
        )
        return LLMClient("ollama", settings.ollama_model, chat)

    if provider == "mock":
        return LLMClient("mock", "template-fallback", None)

    raise ValueError(f"Unknown LLM_PROVIDER={provider!r}. Use openai | groq | ollama | mock.")


# =============================================================================
# Embeddings
# =============================================================================
class HashingEmbeddings(Embeddings):
    """Offline lexical embedder used by LLM_PROVIDER=mock.

    It hashes word unigrams+bigrams into a fixed 1024-dim vector (the
    "hashing trick") and L2-normalises it. There is no semantics, only word
    overlap. That is still enough for the RAG plumbing to return relevant
    policy clauses in tests, with no model download.
    """

    def __init__(self, dim: int = 1024):
        from sklearn.feature_extraction.text import HashingVectorizer

        self._vec = HashingVectorizer(
            n_features=dim, ngram_range=(1, 2), alternate_sign=False, norm="l2", stop_words="english"
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        m = self._vec.transform(texts).toarray().astype(np.float32)
        return m.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class FastEmbedEmbeddings(Embeddings):
    """EMBEDDINGS_PROVIDER=local: FastEmbed runs a small ONNX embedding model
    (default BAAI/bge-small-en-v1.5, ~70 MB, downloaded once from HuggingFace)
    on the CPU, in-process. No server and no GPU, so it works on machines where
    Ollama can't run (e.g. macOS 13, whose Metal driver crashes Ollama)."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        # query_embed applies the model's query prefix (bge models expect one).
        return next(iter(self._model.query_embed(text))).tolist()


@lru_cache(maxsize=1)
def get_embeddings() -> tuple[Embeddings, str]:
    """Return (embedding_model, short_name). The name is used to namespace the
    Chroma collections: vectors from different models have different
    dimensions and meanings, so they must never share a collection."""
    provider = settings.resolved_embeddings_provider()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.openai_embed_model, api_key=settings.openai_api_key), \
            f"openai-{settings.openai_embed_model}"
    if provider == "local":
        return FastEmbedEmbeddings(settings.local_embed_model), f"local-{settings.local_embed_model.split('/')[-1]}"
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.ollama_embed_model, base_url=settings.ollama_base_url), \
            f"ollama-{settings.ollama_embed_model}"
    return HashingEmbeddings(), "mock-hashing"

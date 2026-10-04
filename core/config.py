"""
core/config.py — one place that knows about environment variables and paths.

WHY a central config module?
  Every other module imports `settings` / `PATHS` from here instead of calling
  os.getenv() itself. That gives us:
    * one file to read to learn every knob the system has,
    * no secrets scattered through the code (keys only ever come from .env),
    * paths that work no matter which directory you launch a script from.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Repository root = parent of this `core/` folder.
ROOT = Path(__file__).resolve().parent.parent

# Load .env if present. Real environment variables win over .env values
# (override=False), which is what you want in Docker / CI.
load_dotenv(ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Paths:
    """Every file location the pipeline reads or writes."""

    root: Path = ROOT
    data: Path = ROOT / "data"
    tools: Path = ROOT / "data" / "tools"                      # synthea jar lives here
    synthea_fhir: Path = ROOT / "data" / "synthea_output" / "fhir"
    generated_fhir: Path = ROOT / "data" / "generated_fhir"    # fallback generator output
    planted_fhir: Path = ROOT / "data" / "planted_fhir"        # fraud rings we inject
    processed: Path = ROOT / "data" / "processed"              # tidy CSV tables
    knowledge: Path = ROOT / "data" / "knowledge"              # RAG corpus (hand-written)
    chroma: Path = ROOT / "data" / "chroma"                    # vector DB on disk
    models: Path = ROOT / "brain" / "models"                   # trained model artefacts
    # SQLite decision log. AUDIT_DB_PATH lets tests use a throwaway file.
    audit_db: Path = Path(os.getenv("AUDIT_DB_PATH", str(ROOT / "trust" / "audit.db")))

    def fhir_dirs(self) -> list[Path]:
        """All FHIR sources that exist, in the order the parser should read them."""
        return [p for p in (self.synthea_fhir, self.generated_fhir, self.planted_fhir) if p.exists()]


@dataclass(frozen=True)
class Settings:
    # LLM switch: "openai" | "groq" | "ollama" | "mock"
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "ollama").lower())
    # Embeddings are chosen separately because not every chat provider offers
    # them (Groq doesn't). Empty = follow LLM_PROVIDER (groq -> local).
    # "local" = FastEmbed: a small ONNX model on the CPU, no server needed.
    embeddings_provider: str = field(default_factory=lambda: _env("EMBEDDINGS_PROVIDER").lower())
    llm_temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.1")))

    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4.1-mini"))
    openai_embed_model: str = field(default_factory=lambda: _env("OPENAI_EMBED_MODEL", "text-embedding-3-small"))

    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "openai/gpt-oss-120b"))
    groq_base_url: str = field(default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))

    local_embed_model: str = field(default_factory=lambda: _env("LOCAL_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))

    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.1:8b"))
    ollama_embed_model: str = field(default_factory=lambda: _env("OLLAMA_EMBED_MODEL", "nomic-embed-text"))

    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "change-me-local-only"))

    seed: int = field(default_factory=lambda: int(_env("SEED", "42")))
    claim_years: int = field(default_factory=lambda: int(_env("CLAIM_YEARS", "4")))


    def resolved_embeddings_provider(self) -> str:
        if self.embeddings_provider:
            return self.embeddings_provider
        return "local" if self.llm_provider == "groq" else self.llm_provider


settings = Settings()
PATHS = Paths()

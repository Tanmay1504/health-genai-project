# core/: config, the swappable LLM interface, and RAG

## What it does
| File | Role |
|---|---|
| `config.py` | Reads `.env` once and exposes `settings` (provider, keys, Neo4j, seed) and `PATHS` (every file location). No other module calls `os.getenv`. |
| `llm.py` | **The one LLM interface.** `get_llm()` returns an `LLMClient` with `.complete()` / `.complete_json()` / `.describe()`. `get_embeddings()` returns the matching embedder. |
| `rag.py` | Chunks `data/knowledge/` by markdown headers (one clause per chunk), embeds into **Chroma**, and serves `retrieve(query, collection, k, plan_code)`. |

### Switching providers (`LLM_PROVIDER` in `.env`)
| Value | Chat model | Embeddings |
|---|---|---|
| `openai` | `ChatOpenAI(OPENAI_MODEL)` | `OpenAIEmbeddings(OPENAI_EMBED_MODEL)` |
| `groq` | `ChatOpenAI(GROQ_MODEL, base_url=Groq)`: Groq is OpenAI-compatible | `local`: FastEmbed `bge-small-en-v1.5` on the CPU (Groq has no embeddings API) |
| `ollama` | `ChatOllama(OLLAMA_MODEL)` | `OllamaEmbeddings(OLLAMA_EMBED_MODEL)` |
| `mock` | none: agents use templates | `HashingEmbeddings` (lexical, offline) |

`EMBEDDINGS_PROVIDER` (`local` | `openai` | `ollama` | `mock`) overrides the embeddings column. No key is hard-coded anywhere. Chroma collections are **namespaced by embedder** (e.g. `policies__ollama-nomic-embed-text`), so switching providers never mixes incompatible vectors. Just re-run `--rebuild`.

### Robustness built into `LLMClient`
- Errors such as model down or quota exceeded return `""`, so agents fall back to templates instead of crashing a claim.
- `complete_json` strips ```` ``` ```` fences and leading prose, then returns your `default` if parsing still fails.
- `describe()` feeds the audit log, so every decision records which model produced it.

### RAG details
- Two collections: `policies` (with a `plan_code` filter; `ALL` documents apply to every plan) and `clinical`.
- Chunk ids are stable (`<file>#<n>`), so a citation in the audit log can be traced to exact text.
- If the embedder is unreachable, `retrieve` raises `RAGUnavailable` with a fix-it message, and the API returns it as a 503.

## How to run
```bash
python -m core.rag --rebuild
python -m core.rag --query "cataract sub-limit" --plan SGH-SILVER
python -m core.rag --query "medical necessity of PCI" --collection clinical
```

## How to verify
- `--rebuild` prints chunk counts (~50 policy, ~35 clinical).
- The PED query returns `suraksha_silver_policy#5` (clause 2.2) as the top hit.

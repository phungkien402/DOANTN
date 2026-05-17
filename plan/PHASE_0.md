# PHASE 0 — Project Scaffold

## Goal

Create the full directory structure and skeleton files for the project.
**No logic yet** — only create files, write docstrings describing what each module
does, and prepare the environment so subsequent phases can start coding immediately.

## Tasks

### 1. Create directory structure

```
ehc-helpdesk/
├── adapters/
├── core/
├── data/
├── api/
├── ui/
├── tests/
└── docs/
```

### 2. Create `requirements.txt`

```txt
# LLM & Embeddings
vllm>=0.4.0
sentence-transformers>=2.7.0

# Vector DB
qdrant-client>=1.9.0

# API
fastapi>=0.111.0
uvicorn>=0.29.0
python-multipart>=0.0.9
httpx>=0.27.0

# Utilities
python-dotenv>=1.0.0
pydantic>=2.0.0
loguru>=0.7.0
```

### 3. Create `.env.example`

```env
# Redmine
REDMINE_URL=http://co.ehc.vn:81/redmine
REDMINE_API_KEY=your_api_key_here
REDMINE_PROJECT=ehcfaq

# vLLM
VLLM_BASE_URL=http://localhost:8000
VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct

# Embedding (local HuggingFace model id or path)
EMBED_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=ehc_faq

# RAG settings
RETRIEVER_TOP_K=10
RERANKER_TOP_N=3
CONFIDENCE_THRESHOLD=0.4

# Session
SESSION_MAX_TURNS=10

# Telegram (for testing)
TELEGRAM_BOT_TOKEN=your_token_here

# Zalo OA (production)
ZALO_OA_SECRET=your_secret_here
ZALO_ACCESS_TOKEN=your_token_here
```

### 4. Create `config.py`

Load all variables from `.env` using `python-dotenv`. Export constants used
across the project. Validate on startup — raise a clear error if any required
variable is missing.

### 5. Create skeleton files

Each file needs only:
- A module-level docstring explaining what it does
- Import placeholders
- Class/function stubs with docstrings and `pass` or `...` as body

**`adapters/base_adapter.py`**
```python
"""
BaseAdapter — Abstract interface for all platform adapters.
Every adapter (Zalo, Telegram, Web) must implement 3 methods:
  - parse_message(raw) -> Message | None
  - format_response(answer_text, confidence) -> str
  - send_message(user_id, text) -> None

The RAG Core (core/) must never import from this package.
Only api/routes.py interacts with adapters.
"""
```

**`core/pipeline.py`**
```python
"""
RAG Pipeline orchestrator.
Accepts a standard Message object, runs it through 5 steps:
  Query Rewriter -> Retriever -> Reranker -> Generator -> Confidence Check
Returns an Answer object or delegates to the Fallback Handler.
"""
```

**`data/ingestor.py`**
```python
"""
Ingestor — Fetches FAQ issues from the Redmine API.
Output: list of Document objects {id, subject, description, url, project}
Run standalone: python -m data.ingestor
"""
```

**`data/embedder.py`**
```python
"""
Embedder — Takes a list of Documents, embeds them with bge-m3, stores in Qdrant.
Each chunk is stored with payload: {issue_id, subject, description, project, url, chunk_text}
Embedding dimension: 1024 (bge-m3 output size)
Run standalone: python -m data.embedder
"""
```

**`api/routes.py`**
```python
"""
FastAPI application routes.
POST /webhook/{platform}  — receive messages from Zalo / Telegram / Web
GET  /health              — health check
GET  /admin/logs          — view unanswered / fallback query logs
POST /admin/reindex       — trigger a fresh data pull from Redmine
"""
```

### 6. Create `tests/eval_set.json` stub

```json
[
  {
    "id": "q01",
    "question": "how do I merge duplicate patient records",
    "type": "in_faq",
    "expected_keywords": ["merge", "patient", "step"]
  },
  {
    "id": "q02",
    "question": "where do I print the medication order sheet",
    "type": "in_faq",
    "expected_keywords": ["print", "medication", "order"]
  }
]
```

More cases will be added in Phase 5.

## Done Criteria

- [ ] `pip install -r requirements.txt` completes without errors
- [ ] `python config.py` prints all config values successfully
- [ ] All skeleton files exist and can be imported without errors
- [ ] `.env.example` covers all required variables

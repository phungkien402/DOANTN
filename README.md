# DOANTN — EHC AI Helpdesk

On-premise RAG chatbot for EHC HIS/EMR users (Vietnamese). Supports multi-channel chat (Web, Slack, Telegram, Zalo OA), clarifies ambiguous questions, and auto-creates tickets for unresolved issues.

## Architecture

```
User Question
     |
     v
[Query Analyzer (Intent Guard)] ── off-topic? ──→ [Chat Fallback]
     |
     v
[Fast Retriever: FAQ + HDSD]
     |
     v
[LLM Orchestrator]
  ├─ action=clarify → [Clarify Question] (save fast chunks)
  ├─ action=ticket  → [Ticket Creator]
  └─ action=answer  → [Full Retriever (FAQ or Manual)] → [Rerank] → [Synthesizer]
                                               ├─ low confidence → [Ticket Creator]
                                               └─ confident → [Generator]
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| LLM | Qwen2.5-7B-Instruct via vLLM |
| Orchestrator | LangGraph + custom LLM router |
| Embedding | BAAI/bge-m3 (1024-dim) |
| Reranker | BAAI/bge-reranker-v2-m3 |
| Vector DB | Qdrant (doantn_faq + ehc_manual) |
| API | FastAPI + Uvicorn |
| Queue | Redis + RQ (Telegram async) |
| Chat platforms | Web, Slack, Telegram, Zalo OA |
| Data sources | Redmine FAQ + HDSD manuals (Markdown) |
| GPU | 2x Tesla V100 16GB (for vLLM) |
| CPU | bge-m3 + reranker (frees GPU VRAM) |

## Project Structure

```
DOANTN/
├── core/               # RAG + LangGraph agent
│   ├── langgraph_agent.py
│   ├── orchestrator.py
│   ├── intent_guard.py
│   ├── retriever.py
│   ├── reranker.py
│   ├── generator.py
│   └── tools/           # search_faq, search_manual, create_ticket
├── data/               # Data ingestion + storage
│   ├── ingestor.py
│   ├── embedder.py
│   ├── ingest_manual.py
│   ├── hdsd/            # Markdown manuals
│   ├── tickets.db
│   └── unanswered.jsonl
├── adapters/           # Platform adapters
│   ├── web_adapter.py
│   ├── slack_adapter.py
│   ├── telegram_adapter.py
│   └── zalo_adapter.py
├── api/                # FastAPI gateway
│   ├── routes.py
│   ├── session.py
│   └── logger.py
├── workers/            # RQ workers
│   └── pipeline_worker.py
├── ui/                 # Web chat + admin dashboard
│   ├── index.html
│   └── dashboard.html
├── deploy/             # systemd service files
│   ├── ehc-vllm.service
│   └── doantn.service
├── scripts/            # Operational scripts
│   ├── monitor.py
│   ├── eval.py
│   └── finetune_bge.py
├── tests/              # Evaluation
│   ├── eval_set.json
│   ├── evaluate.py
│   └── debug_query.py
├── config.py
├── .env.example
└── requirements.txt
```

## Quick Start

### Prerequisites

- Ubuntu server with 2x NVIDIA V100 (or equivalent, 32GB+ VRAM total)
- Python 3.12+
- Docker (for Qdrant)
- Redmine instance with FAQ project

### 1. Clone and install

```bash
git clone https://github.com/phungkien402/DOANTN.git
cd DOANTN
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Redmine URL, API key, Slack tokens, etc.
```

### 3. Start infrastructure

```bash
# Qdrant
docker run -d -p 6333:6333 qdrant/qdrant

# vLLM (shared service — port 8000, used by this project and others)
sudo systemctl start ehc-vllm

# Redis (required for async Telegram queue)
docker run -d -p 6379:6379 redis:7
```

### 4. Ingest and embed data

```bash
python -m data.embedder
# Fetches FAQ entries from Redmine, embeds with bge-m3, stores in doantn_faq

python -m data.ingest_manual
# Chunks HDSD manuals under data/hdsd and stores in ehc_manual
```

### 5. Start the API server

```bash
uvicorn api.routes:app --host 0.0.0.0 --port 8001
```

### 6. Open the web UI

Navigate to `http://your-server:8001` in a browser.

### 7. (Optional) Start Telegram worker

```bash
rq worker ehc-queue --url redis://localhost:6379
```

## Shared vLLM Service

The vLLM inference server runs as a standalone systemd service (`ehc-vllm.service`) on port 8000. It is shared across multiple projects on the same server — not exclusive to this helpdesk. Any service needing LLM inference connects to `http://localhost:8000/v1`.

```bash
# Manage the shared vLLM service
sudo systemctl start ehc-vllm
sudo systemctl status ehc-vllm
journalctl -u ehc-vllm -f
```

## Production Deployment (systemd)

```bash
# Install service files
sudo cp deploy/ehc-vllm.service /etc/systemd/system/
sudo cp deploy/doantn.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ehc-vllm doantn
sudo systemctl start ehc-vllm doantn

# Check status
sudo systemctl status ehc-vllm
sudo systemctl status doantn

# View logs
journalctl -u ehc-vllm -f
journalctl -u doantn -f
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web Chat UI (includes admin tab) |
| GET | `/health` | Health check |
| POST | `/webhook/{platform}` | Webhook for web, slack, telegram, zalo |
| GET | `/admin/logs` | Query logs (optional `?fallback_only=true`) |
| POST | `/admin/reindex` | Trigger FAQ reindex |
| POST | `/admin/maintenance` | Toggle maintenance mode |
| GET | `/tickets` | All tickets from SQLite |
| GET | `/unanswered` | Unanswered questions from jsonl |

## Slack Slash Commands

| Command | Description |
|---------|-------------|
| `/health` | Check vLLM + Qdrant + API |
| `/stats` | Stats for last 24 hours |
| `/top` | Top 5 questions (7 days) |
| `/clear` | Clear user session history |
| `/refresh` | Trigger reindex (admin only) |
| `/create_ticket` | Log a manual ticket |

## Ticketing and Logs

- Low-confidence or unresolved queries create tickets in `data/tickets.db`.
- Each ticket is also appended to `data/unanswered.jsonl` for review.
- All queries are logged to `logs/queries.jsonl`.

## Observability (optional)

Set Langfuse keys to enable span tracing in the LangGraph agent.

## Evaluation

```bash
# Run full evaluation (22 questions, expects >= 80% in-FAQ accuracy)
python -m tests.evaluate

# Debug a single query (shows all pipeline steps)
python -m tests.debug_query "in bang ke kham benh o dau"
```

### Results

| Metric | Value |
|--------|-------|
| In-FAQ accuracy | 100% (12/12) |
| Colloquial accuracy | 100% (5/5) |
| Fallback accuracy | 100% (3/3) |
| Ambiguous handling | 100% (2/2) |
| Avg response time | 4.61s |

## Key Design Decisions

- **No LangChain/LlamaIndex** — plain Python for full control and easy debugging
- **LangGraph orchestration** — LLM router decides answer vs clarify vs ticket
- **Dual-source retrieval** — FAQ + HDSD manuals, chosen per query intent
- **Intent Guard** — LLM classifier filters off-topic queries before hitting the RAG pipeline
- **CPU for embedding/reranker** — frees GPU VRAM for vLLM inference
- **Cross-encoder reranker** — improves retrieval precision before generation
- **Module-level singletons** — models loaded once at import, not per-request
- **Strict grounding prompt** — LLM answers only from retrieved context

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| REDMINE_URL | Yes | — | Redmine base URL |
| REDMINE_API_KEY | Yes | — | Redmine API key |
| REDMINE_PROJECT | No | ehcfaq | Redmine project identifier |
| VLLM_BASE_URL | No | http://localhost:8000 | vLLM server URL |
| VLLM_MODEL | No | Qwen/Qwen2.5-7B-Instruct | Model name |
| EMBED_MODEL | No | BAAI/bge-m3 | Embedding model |
| RERANKER_MODEL | No | BAAI/bge-reranker-v2-m3 | Reranker model |
| QDRANT_URL | No | http://localhost:6333 | Qdrant URL |
| QDRANT_COLLECTION | No | doantn_faq | Collection name |
| RETRIEVER_TOP_K | No | 10 | Chunks to retrieve |
| RERANKER_TOP_N | No | 3 | Chunks after reranking |
| CONFIDENCE_THRESHOLD | No | 0.4 | Min reranker score |
| SESSION_MAX_TURNS | No | 10 | Max conversation turns |
| SLACK_BOT_TOKEN | No | — | Slack bot OAuth token (required if using Slack) |
| SLACK_SIGNING_SECRET | No | — | Slack request signing secret (required if using Slack) |
| SLACK_ADMIN_USERS | No | — | Comma-separated Slack user IDs for admin |
| ADMIN_TOKEN | No | — | Token for admin API endpoints |
| MAINTENANCE_MODE | No | false | Start in maintenance mode |
| TELEGRAM_BOT_TOKEN | No | — | Telegram bot token (required if using Telegram) |
| ZALO_OA_SECRET | No | — | Zalo OA webhook secret (required if using Zalo OA) |
| ZALO_ACCESS_TOKEN | No | — | Zalo OA access token (required if using Zalo OA) |
| REDIS_URL | No | redis://localhost:6379 | Redis URL for queue |
| LANGFUSE_PUBLIC_KEY | No | — | Langfuse public key |
| LANGFUSE_SECRET_KEY | No | — | Langfuse secret key |
| LANGFUSE_HOST | No | http://localhost:3000 | Langfuse host |

## License

Internal use only — EHC Healthcare.
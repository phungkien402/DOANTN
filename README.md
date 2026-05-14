# EHC AI Helpdesk

On-premise RAG chatbot for doctors using the EHC electronic medical record software. Answers technical support questions by retrieving relevant FAQ entries from Redmine and generating grounded Vietnamese responses.

## Architecture

```
User Question
     |
     v
[Query Rewriter] --> [Retriever] --> [Reranker] --> [Confidence Check]
    (vLLM)          (bge-m3 +       (bge-reranker        |
                     Qdrant)          -v2-m3)       -----+-----
                                                    |         |
                                                    v         v
                                              [Generator] [Fallback]
                                                (vLLM)    Handler
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| LLM | Qwen2.5-7B-Instruct via vLLM |
| Embedding | BAAI/bge-m3 (1024-dim) |
| Reranker | BAAI/bge-reranker-v2-m3 |
| Vector DB | Qdrant |
| API | FastAPI + Uvicorn |
| Data source | Redmine FAQ project (464 entries) |
| GPU | 2x Tesla V100 16GB (for vLLM) |
| CPU | bge-m3 + reranker (frees GPU VRAM) |

## Project Structure

```
ehc-helpdesk/
├── core/               # RAG pipeline modules
│   ├── query_rewriter.py
│   ├── retriever.py
│   ├── reranker.py
│   ├── generator.py
│   ├── confidence.py
│   ├── fallback.py
│   └── pipeline.py
├── data/               # Data ingestion layer
│   ├── ingestor.py
│   ├── embedder.py
│   └── reindex.py
├── adapters/           # Platform adapters
│   ├── telegram_adapter.py
│   ├── zalo_adapter.py
│   └── web_adapter.py
├── api/                # FastAPI gateway
│   ├── routes.py
│   ├── session.py
│   └── logger.py
├── ui/                 # Web interface
│   └── index.html
├── tests/              # Evaluation
│   ├── eval_set.json
│   ├── evaluate.py
│   └── debug_query.py
├── deploy/             # systemd service files
│   ├── ehc-vllm.service
│   └── ehc-helpdesk.service
├── docs/
│   └── PROGRESS.md
├── config.py
├── .env.example
└── requirements.txt
```

## Quick Start

### Prerequisites

- Ubuntu server with 2x NVIDIA V100 (or equivalent, 32GB+ VRAM total)
- Python 3.10+
- Docker (for Qdrant)
- Redmine instance with FAQ project

### 1. Clone and install

```bash
git clone https://github.com/phungkien402/EHC_HELPDESK.git
cd EHC_HELPDESK/ehc-helpdesk
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Redmine URL, API key, etc.
```

### 3. Start infrastructure

```bash
# Qdrant
docker run -d -p 6333:6333 qdrant/qdrant

# vLLM (uses both GPUs)
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-7B-Instruct \
    --tensor-parallel-size 2 \
    --dtype half \
    --gpu-memory-utilization 0.90
```

### 4. Ingest and embed FAQ data

```bash
python -m data.embedder
# Fetches 464 FAQ entries from Redmine, embeds with bge-m3, stores in Qdrant
```

### 5. Start the API server

```bash
uvicorn api.routes:app --host 0.0.0.0 --port 8080
```

### 6. Open the web UI

Navigate to `http://your-server:8080` in a browser.

## Production Deployment (systemd)

```bash
# Install service files
sudo cp deploy/ehc-vllm.service /etc/systemd/system/
sudo cp deploy/ehc-helpdesk.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ehc-vllm ehc-helpdesk
sudo systemctl start ehc-vllm ehc-helpdesk

# Check status
sudo systemctl status ehc-vllm
sudo systemctl status ehc-helpdesk

# View logs
journalctl -u ehc-vllm -f
journalctl -u ehc-helpdesk -f
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web Chat UI |
| GET | `/health` | Health check |
| POST | `/webhook/web` | Web chat (`{"user_id": "...", "text": "..."}`) |
| POST | `/webhook/telegram` | Telegram webhook |
| POST | `/webhook/zalo` | Zalo OA webhook |
| GET | `/admin/logs` | Query logs (optional `?fallback_only=true`) |
| POST | `/admin/reindex` | Trigger FAQ reindex |

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

- **No LangChain/LlamaIndex** - plain Python for full control and easy debugging
- **CPU for embedding/reranker** - frees all GPU VRAM for vLLM inference
- **Cross-encoder reranker** - dramatically improves retrieval quality (vector 0.73 -> reranker 0.99)
- **Module-level singletons** - models loaded once at import, not per-request
- **Strict grounding prompt** - LLM answers only from retrieved context, no hallucination
- **Graceful degradation** - pipeline works without vLLM (returns retrieved chunks directly)

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| REDMINE_URL | Yes | - | Redmine base URL |
| REDMINE_API_KEY | Yes | - | Redmine API key |
| REDMINE_PROJECT | No | ehcfaq | Redmine project identifier |
| VLLM_BASE_URL | No | http://localhost:8000 | vLLM server URL |
| VLLM_MODEL | No | Qwen/Qwen2.5-7B-Instruct | Model name |
| EMBED_MODEL | No | BAAI/bge-m3 | Embedding model |
| RERANKER_MODEL | No | BAAI/bge-reranker-v2-m3 | Reranker model |
| QDRANT_URL | No | http://localhost:6333 | Qdrant URL |
| QDRANT_COLLECTION | No | ehc_faq | Collection name |
| RETRIEVER_TOP_K | No | 10 | Chunks to retrieve |
| RERANKER_TOP_N | No | 3 | Chunks after reranking |
| CONFIDENCE_THRESHOLD | No | 0.4 | Min reranker score |
| TELEGRAM_BOT_TOKEN | No | - | Telegram bot token |
| ZALO_OA_SECRET | No | - | Zalo OA app secret |
| ZALO_ACCESS_TOKEN | No | - | Zalo OA access token |

## License

Internal use only - EHC Healthcare.
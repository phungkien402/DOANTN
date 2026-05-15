# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EHC AI Helpdesk — an on-premise RAG chatbot that answers doctors' questions about EHC electronic medical record software by looking up an internal FAQ knowledge base stored in Redmine. Runs 100% on-premise (Dell R730xd, 2x V100 16GB, Ubuntu). Vietnamese language is primary.

## Architecture

```
Platform (Zalo/Telegram/Web) → Adapter → FastAPI Gateway → RAG Pipeline → Response
```

RAG Pipeline (7 steps in `core/pipeline.py` — iterative retrieval):
1. Fast Retrieve (`core/retriever.py`) — embed original query, get top 3 chunks (no rerank)
2. Contextual Intent Analysis (`core/query_rewriter.py:analyze_intent`) — LLM analyzes intent WITH chunk context
3. Query Rewrite (`core/query_rewriter.py:rewrite`) — colloquial Vietnamese → formal query, informed by grounded intent
4. Full Retrieve (`core/retriever.py`) — embed rewritten query, fetch Top-K from Qdrant
5. Reranker (`core/reranker.py`) — cross-encoder rescore with bge-reranker-v2-m3, keep Top-N
6. Confidence Check (`core/confidence.py`) — route to fallback if top score < threshold
7. Generator (`core/generator.py`) — vLLM (Qwen2.5-7B-Instruct) generates grounded answer

Why iterative: LLM needs EHC context to analyze intent accurately — blind intent analysis is ineffective for domain-specific terminology. The fast retrieve gives the LLM real FAQ context before it reasons about the user's problem.

Key separation: `core/` never imports from `adapters/`. Only `api/routes.py` bridges them.

## Tech Stack

- LLM: Qwen2.5-7B-Instruct via vLLM (OpenAI-compatible API at `VLLM_BASE_URL`)
- Embeddings: BAAI/bge-m3 (1024-dim vectors)
- Reranker: BAAI/bge-reranker-v2-m3
- Vector DB: Qdrant
- Backend: Python 3.11 + FastAPI
- No LangChain/LlamaIndex — plain Python, every step inspectable

## Commands

**IMPORTANT: The shell environment lacks PATH — only `/bin/bash` works. Use the patterns below.**

```bash
# --- Shell/system commands (git, ls, find, etc.) ---
# Always use /bin/bash -c with exported PATH:
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && <command>"

# Examples:
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && git status"
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && ls -la"

# --- Python commands ---
# Use run.sh (sets PATH + cd into project + runs python3):
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m data.ingestor
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m data.embedder
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m data.reindex
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m data.reindex --diff
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m core.pipeline
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m tests.evaluate
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m tests.debug_query "your question"

# Server
/bin/bash /home/phungkien/EHC_HELPDESK/ehc-helpdesk/run.sh -m uvicorn api.routes:app --host 0.0.0.0 --port 8080

# Prerequisites
# Qdrant: docker run -d -p 6333:6333 --name qdrant qdrant/qdrant
# vLLM:  /bin/bash run.sh -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct
```

## Git Workflow

```bash
# Git repo is at /home/phungkien/EHC_HELPDESK/ehc-helpdesk (not the parent folder)
# Remote: git@github.com:phungkien402/EHC_HELPDESK.git (SSH)

# Always run git from the repo directory:
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /home/phungkien/EHC_HELPDESK/ehc-helpdesk && git add -A && git status"
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /home/phungkien/EHC_HELPDESK/ehc-helpdesk && git commit -m 'your message'"
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /home/phungkien/EHC_HELPDESK/ehc-helpdesk && git push origin main"
```

### Branching Rules

- For new features or experimental changes, always create a new branch first:
  `git checkout -b feature/<short-description>`
- Only merge to main after the reviewer (via Cowork) approves
- Branch naming convention: `feature/<name>`, `fix/<name>`, `experiment/<name>`
- Never commit experimental or untested changes directly to main

## Critical Design Decisions

- **Embedding text**: Always embed `subject + description` together (`f"Câu hỏi: {subject}\nHướng dẫn: {description}"`). Descriptions average only 123 chars; subject carries most semantic meaning.
- **No chunking needed**: Each Redmine issue = one chunk (descriptions are too short to split).
- **Upsert, not insert**: `data/embedder.py` uses Qdrant upsert so re-running is always safe.
- **Arrow normalization**: Redmine descriptions use mixed `-->`, `=>`, `==>` — normalize all to `→`.
- **Confidence threshold**: 0.40 default. Below this → fallback (no answer generated).
- **Every module has `__main__`**: Each file must be independently runnable for testing.
- **Verbose logging**: Every pipeline step must log inputs/outputs with scores.

## Config

All config via `.env` file loaded by `config.py`. Key variables:
- `REDMINE_URL`, `REDMINE_API_KEY`, `REDMINE_PROJECT`
- `VLLM_BASE_URL`, `VLLM_MODEL`
- `EMBED_MODEL`, `RERANKER_MODEL`
- `QDRANT_URL`, `QDRANT_COLLECTION`
- `RETRIEVER_TOP_K` (default 10), `RERANKER_TOP_N` (default 3), `CONFIDENCE_THRESHOLD` (default 0.4)

## Slack Slash Commands

Implemented in `adapters/slack_adapter.py` → `handle_slash_command()`, routed from `api/routes.py` when Slack sends `application/x-www-form-urlencoded` POST to `/webhook/slack`.

| Command | Description | Auth |
|---------|-------------|------|
| `/health` | Check vLLM, Qdrant, API status | Any user |
| `/stats` | Last 24h: total questions, success rate, avg confidence | Any user |
| `/top` | Top 5 most asked questions (7 days) | Any user |
| `/clear` | Clear calling user's session history | Any user |
| `/refresh` | Trigger Redmine reindex in background | Admin only (`SLACK_ADMIN_USERS`) |
| `/create_ticket` | Log issue for manual review | Any user |

Config: `SLACK_ADMIN_USERS` env var — comma-separated Slack user IDs (e.g. `U12345,U67890`).

Response format: ephemeral JSON (`{"response_type": "ephemeral", "text": "..."}`). All commands respond within 3s.

## Build Phases

Phases are documented in `plan/PHASE_0.md` through `plan/PHASE_5.md`. Complete each phase sequentially — later phases depend on earlier ones being verified.

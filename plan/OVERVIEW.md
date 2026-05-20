# EHC AI Helpdesk — Project Overview

## Background

EHC is a company that deploys Electronic Medical Record (EMR) software for hospitals and
clinics. End users are **doctors** — they frequently struggle with new software workflows
and send repetitive questions to the helpdesk team via Zalo group chats or phone calls.

Real examples of questions doctors ask:
- "how do I merge duplicate patient records?"
- "where do I print the daily medication order sheet?"
- "what do I do when a medical record is locked?"

The company already maintains an internal **FAQ knowledge base on Redmine** (project
`ehcfaq`) that documents solutions to common issues. The helpdesk team currently answers
these questions manually — wasting time on repetitive, already-documented problems.

## Goal

Build an **internal AI chatbot** that automatically looks up the FAQ and answers doctors
via Zalo OA. When no answer is found, the bot asks a clarifying question; if it still
cannot help, it logs the question and escalates to the helpdesk team.

The system must run **100% on-premise** on EHC's internal server — no data leaves the
network, ensuring medical record privacy.

## Hardware

| Component | Spec |
|---|---|
| Server | Dell R730xd |
| GPU | 2x Tesla V100 16GB |
| RAM | 128GB |
| OS | Ubuntu |

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| LLM serving | vLLM | Best throughput for concurrent requests on V100 |
| LLM model | Qwen2.5-7B-Instruct | Proven, good Vietnamese, fast on V100 — upgrade to 14B if quality is insufficient |
| Embedding model | bge-m3 (local) | Best multilingual model, strong Vietnamese support |
| Reranker model | bge-reranker-v2-m3 | Same family as bge-m3, runs on CPU |
| Vector database | Qdrant | Better metadata filtering than ChromaDB, easier to scale |
| Backend | Python 3.11 + FastAPI | Standard for Python AI services |
| RAG framework | **Plain Python — no LangChain / LlamaIndex** | Full control, easy to debug, every step is inspectable |

> **Note on vLLM + V100:** Tesla V100 uses CUDA 7.0. Verify vLLM version compatibility
> before starting. If vLLM has issues, fall back to Ollama for the first iteration.

> **Note on model size:** Start with `Qwen2.5-7B-Instruct`. The FAQ answers are short
> (avg 123 chars) and straightforward — 7B is sufficient. Only move to 14B if evaluation
> scores in Phase 5 fall below the 80% threshold.

## Redmine FAQ Data — Key Facts

Fetched and analyzed on 2026-05-13. These facts must inform the ingestor and embedder.

| Metric | Value |
|---|---|
| Total issues | 468 |
| Usable issues (description ≥ 20 chars) | 455 (97%) |
| To skip (empty or too short) | 13 (3%) |
| Average description length | 123 characters |
| Max description length | 672 characters |
| Date range | 2025-04-21 → 2026-05-08 |

### Description style (not uniform — must normalize)

Most FAQ entries are short navigation paths, not paragraphs:

```
Module Hành chính => In --> Bảng kê khám bệnh, chữa bệnh
```
```
Vào module Điều trị --> menu --> dược lầm sàng --> xem tồn kho thuốc
```

- 38% use `-->` arrow style
- 14% use `=>` arrow style
- Many mix both in the same entry
- A few entries have `Nguyên nhân:` / `Hướng giải quyết:` structure

### Critical implication for chunking

Because descriptions are very short (avg 123 chars), **the `subject` field is
essential for retrieval quality**. The subject is usually a natural-language question
that closely matches how doctors phrase their queries. Always embed
`subject + description` together, never description alone.

```python
# Correct — always do this
text = f"Câu hỏi: {doc.subject}\nHướng dẫn: {doc.description}"

# Wrong — loses the most matchable part
text = doc.description
```

## System Architecture

```
[Zalo OA / Telegram / Web UI]
         │
         ▼
  [Adapter Layer]              ← platform-specific, easy to add new channels
         │  parse_message() → standard Message object
         ▼
  [FastAPI Gateway]            ← routing, auth, session management, logging
         │
         ▼
  [RAG Core Pipeline]          ← knows nothing about platforms
    1. Query Rewriter           ← normalize colloquial Vietnamese questions
    2. Retriever                ← fetch Top-K chunks from Qdrant
    3. Reranker                 ← re-score and filter to Top-N best chunks
    4. Generator (vLLM)         ← generate grounded answer from context
    5. Confidence Check         ← confident enough? → answer / fallback
         │
         ▼
  [Fallback Handler]           ← clarify → log → escalate to helpdesk
         │
         ▼
  [Adapter Layer]              ← format_response() → send back to platform
```

## Project Structure

```
ehc-helpdesk/
├── adapters/
│   ├── base_adapter.py        # Abstract interface — 3 required methods
│   ├── zalo_adapter.py
│   ├── telegram_adapter.py
│   └── web_adapter.py
├── core/
│   ├── models.py              # Shared dataclasses: Message, Answer, RetrievedChunk
│   ├── pipeline.py            # Orchestrates all 5 pipeline steps
│   ├── query_rewriter.py
│   ├── retriever.py
│   ├── reranker.py
│   ├── generator.py
│   ├── confidence.py
│   └── fallback.py
├── data/
│   ├── ingestor.py            # Pull FAQ issues from Redmine API
│   ├── embedder.py            # Embed documents and store in Qdrant
│   └── reindex.py             # Full or incremental reindex script
├── api/
│   ├── routes.py              # FastAPI endpoints
│   ├── session.py             # Conversation history per user
│   └── logger.py              # Query logging and fallback alerts
├── ui/
│   └── index.html             # Web chat UI + admin panel (single file)
├── tests/
│   └── eval_set.json          # Evaluation question set
├── config.py                  # Load and validate env vars
├── .env.example
└── requirements.txt
```

## Coding Principles

1. **Every module is independently testable** — each file has `if __name__ == "__main__"`
   so it can be run and tested in isolation before integration
2. **Verbose logging at every step** — always print: original question, rewritten question,
   retrieved chunks with scores, exact prompt sent to LLM, LLM output, confidence score
3. **No hallucination** — LLM system prompt must strictly instruct: answer only from the
   provided context; if context is insufficient, say so explicitly
4. **Adapters are fully decoupled** — `core/` must never import anything from `adapters/`;
   only `api/routes.py` uses adapters
5. **Config via .env only** — no hardcoded API keys, URLs, or model names in source code

## Data Source

- **Redmine API**: `http://co.ehc.vn:81/redmine`
  - API key: loaded from `.env` (`REDMINE_API_KEY`)
  - Project: `ehcfaq`
  - Fields to extract: `id`, `subject`, `description`
  - Skip issues where description is empty or shorter than 20 characters
  - Each issue = one chunk, stored with metadata: `{issue_id, subject, project, url}`

## Build Order

Complete and verify each phase before moving to the next.

| File | Content |
|---|---|
| `PHASE_0.md` | Project scaffold and structure |
| `PHASE_1.md` | Data layer: Ingestor + Embedder |
| `PHASE_2.md` | RAG Core pipeline |
| `PHASE_3.md` | Adapter layer + FastAPI gateway |
| `PHASE_4.md` | Web Chat UI + Admin panel |
| `PHASE_5.md` | RAG quality evaluation |

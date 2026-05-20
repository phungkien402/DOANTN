# PHASE 2 — RAG Core Pipeline

## Goal

This is the **most critical phase**. When complete, the following must work:

```bash
python -m core.pipeline
# type a question, receive an answer, see every pipeline step logged
```

The pipeline must **log every step in detail** — this is the primary reason
previous builds were unreliable: when things went wrong, there was no way to
tell where in the pipeline the failure occurred.

## Prerequisites

- Phase 1 complete — Qdrant is populated with FAQ data
- vLLM server running:
  ```bash
  python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct
  ```

---

## Shared Data Models

Define these in `core/models.py` and import them everywhere:

```python
@dataclass
class Message:
    user_id: str
    session_id: str
    text: str
    timestamp: float
    platform: str           # "zalo", "telegram", "web"

@dataclass
class RetrievedChunk:
    text: str
    score: float            # reranker score (final relevance score)
    metadata: dict          # {issue_id, subject, project, url}

@dataclass
class Answer:
    text: str
    confidence: float       # 0.0 – 1.0, from top reranker score
    source_chunks: list[RetrievedChunk]
    is_fallback: bool
```

---

## Step 1 — `core/query_rewriter.py`

### Why this step exists

Doctors ask in short, colloquial Vietnamese:
*"merge patient records how?"*

The FAQ is written in formal language:
*"Instructions for merging duplicate patient records"*

These two strings have low embedding similarity even though they mean the same
thing. The rewriter uses the LLM to bridge that gap before the retrieval step.

### Prompt

```
System: You are a query normalization assistant. Convert colloquial, shorthand
questions about the EHC electronic medical record software into clear, complete
formal questions. Return only the rewritten question — no explanation.

User: merge patient records how?
Assistant: How do I merge duplicate patient records in the EHC system?
```

### Log output

```
[REWRITER] Original : "merge patient records how?"
[REWRITER] Rewritten: "How do I merge duplicate patient records in the EHC system?"
```

---

## Step 2 — `core/retriever.py`

### Responsibility

Embed the rewritten question and fetch the Top-K most similar chunks from
Qdrant.

### Details

- Use the **same** `bge-m3` model used during indexing — mismatched models
  will silently produce poor results
- `TOP_K` comes from config (default: 10)
- Return a list of `RetrievedChunk` with the raw cosine similarity score
  (this is not the final score — reranker will rescore)

### Log output

```
[RETRIEVER] Query: "How do I merge duplicate patient records in the EHC system?"
[RETRIEVER] Top 10 chunks retrieved:
  #1  score=0.82 | "Merge patient records: Go to Administration module..."
  #2  score=0.71 | "Delete duplicate patient: Go to patient list..."
  ...
```

---

## Step 3 — `core/reranker.py`

### Why this step is the most important fix

Qdrant returns results ranked by vector similarity — it can return chunks
that are *loosely related* but not actually the best answer. The reranker uses
a cross-encoder model to read each (question, chunk) pair directly and produces
a much more accurate relevance score.

**This step is the primary fix for the "chaotic" outputs in previous builds.**

### Model

`BAAI/bge-reranker-v2-m3` — runs locally, low GPU footprint.

### Implementation

```python
# Input:  original question + top-10 chunks from retriever
# Output: top-3 chunks re-sorted by reranker score

pairs = [(question, chunk.text) for chunk in chunks]
scores = reranker.compute_score(pairs)
# sort descending by score, keep TOP_N
```

### Log output

```
[RERANKER] Input : 10 chunks
[RERANKER] After reranking:
  #1  score=0.94 | "Merge patient records: Go to Administration module..."
  #2  score=0.43 | "Delete duplicate patient: Go to patient list..."
  #3  score=0.21 | "Search patient: Enter name or ID..."
[RERANKER] Top score: 0.94  (threshold: 0.40) → CONFIDENT
```

---

## Step 4 — `core/generator.py`

### Responsibility

Accept the rewritten question and top reranked chunks, call vLLM via its
OpenAI-compatible API, and return a grounded answer.

vLLM exposes an OpenAI-compatible API — use the `openai` SDK pointing to the
local `VLLM_BASE_URL`.

### System prompt

```
You are a technical support assistant for the EHC electronic medical record software.
Your job is to answer doctors' questions based SOLELY on the reference documentation
provided in the CONTEXT section below.

Rules you must follow:
1. Use ONLY information present in the CONTEXT. Do not add anything from outside it.
2. If the CONTEXT does not contain enough information to answer, say exactly:
   "I could not find documentation for this issue."
3. Keep answers concise and clear. Use numbered steps when describing a procedure.
4. Do not ask the user follow-up questions unless the question is genuinely ambiguous.
```

### User prompt template

```
CONTEXT:
{chunk_1_text}

---

{chunk_2_text}

---

{chunk_3_text}

---

QUESTION: {rewritten_question}
```

### Log output

```
[GENERATOR] Context chunks : 3
[GENERATOR] Prompt length  : 1240 tokens
[GENERATOR] Response       : "To merge duplicate patient records, follow these steps:..."
[GENERATOR] Tokens used    : 340
```

---

## Step 5 — `core/confidence.py` + `core/fallback.py`

### Confidence check

```python
def is_confident(top_chunk: RetrievedChunk) -> bool:
    return top_chunk.score >= CONFIDENCE_THRESHOLD   # default: 0.40
```

If the top reranker score is below the threshold → not confident → route to
fallback instead of generating an answer.

### Fallback handler (`core/fallback.py`)

Handle three cases in order:

```
Case 1 — Question is ambiguous (< 5 words or unclear intent):
  Response: "Could you describe the issue in more detail?
             For example: what message is shown on screen,
             and which module are you working in?"

Case 2 — Already asked for clarification in this session and still no match:
  Response: "This issue is not covered in my current documentation.
             I have logged your question and will escalate it to
             the helpdesk team for follow-up."
  Action  : write {user_id, question, timestamp} to log file

Case 3 — Question is clear but not found in FAQ:
  Same response and action as Case 2.
```

---

## `core/pipeline.py` — Orchestrator

```python
def run(message: Message, session_history: list) -> Answer:
    # Step 1: normalize the question
    rewritten = query_rewriter.rewrite(message.text)

    # Step 2: fetch candidate chunks
    chunks = retriever.retrieve(rewritten, top_k=TOP_K)

    # Step 3: rerank candidates
    ranked_chunks = reranker.rerank(rewritten, chunks, top_n=TOP_N)

    # Step 4: check confidence before generating
    if not confidence.is_confident(ranked_chunks[0]):
        return fallback.handle(message, session_history)

    # Step 5: generate grounded answer
    answer_text = generator.generate(rewritten, ranked_chunks)

    return Answer(
        text=answer_text,
        confidence=ranked_chunks[0].score,
        source_chunks=ranked_chunks,
        is_fallback=False
    )
```

### Interactive `__main__` for testing

```python
if __name__ == "__main__":
    print("=== EHC RAG Pipeline — Interactive Test ===")
    print("Type a question (or 'quit' to exit):\n")
    while True:
        q = input("You: ").strip()
        if q == "quit":
            break
        msg = Message(
            user_id="test", session_id="s1",
            text=q, timestamp=time.time(), platform="web"
        )
        answer = run(msg, [])
        print(f"\nBot: {answer.text}")
        print(f"     [confidence={answer.confidence:.2f}  fallback={answer.is_fallback}]\n")
```

---

## Done Criteria

- [ ] `python -m core.pipeline` runs and answers questions interactively
- [ ] Every step is logged: rewrite → retrieve (with scores) → rerank (with scores) → generate
- [ ] Question covered by FAQ → correct answer, cites source FAQ subject
- [ ] Question not in FAQ → fallback response, no hallucination
- [ ] Ambiguous short question → asks for clarification, does not guess
- [ ] Test at least 10 real questions from `tests/eval_set.json` manually

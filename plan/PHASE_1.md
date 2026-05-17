# PHASE 1 — Data Layer: Ingestor + Embedder

## Goal

By the end of this phase, the following commands must work:

```bash
python -m data.ingestor    # pull FAQ from Redmine, print issue count
python -m data.embedder    # embed and store in Qdrant, print chunk count
```

Qdrant must be populated with real data before Phase 2 can begin retrieval.

## Prerequisites

- Phase 0 complete
- `.env` filled with `REDMINE_URL`, `REDMINE_API_KEY`
- Qdrant running locally: `docker run -p 6333:6333 qdrant/qdrant`
- vLLM does not need to be running in this phase

---

## Real data facts (verified 2026-05-13)

Before writing any code, understand what the actual Redmine data looks like:

| Fact | Value |
|---|---|
| Total issues | 468 |
| Usable (description ≥ 20 chars) | 455 |
| Skip (empty / too short) | 13 |
| Average description length | **123 characters** — very short |
| Description style | Navigation paths using `-->` and `=>` arrows, mixed |

**Most descriptions look like this:**
```
Module Hành chính => In --> Bảng kê khám bệnh, chữa bệnh
```
```
Vào module Điều trị --> menu --> dược lầm sàng --> xem tồn kho thuốc
```

Because descriptions are so short, the `subject` field carries most of the
semantic meaning. **Never embed description alone.**

---

## 1. `data/ingestor.py`

### Responsibility

Fetch all issues from the Redmine API, clean and normalize the text,
return a list of `Document` objects.

### Redmine API

```
GET {REDMINE_URL}/issues.json
    ?project_id=ehcfaq
    &limit=100
    &offset=0
    &key={REDMINE_API_KEY}
```

Paginate by incrementing `offset` by 100 until the returned `issues` list
is empty. Total expected: ~468 issues across 5 pages.

### Data model

```python
@dataclass
class Document:
    issue_id: int
    subject: str        # issue title — natural language question
    description: str    # navigation path / instructions (short)
    project: str        # "ehcfaq"
    url: str            # e.g. http://co.ehc.vn:81/redmine/issues/38996
```

### Skip conditions

Skip the issue entirely if:
- `description` is empty or whitespace-only
- `description` has fewer than 20 characters after stripping

Log each skipped issue: `[SKIP] id=42709 subject="x" reason="empty description"`

### Text normalization

Apply to both `subject` and `description`:

```python
def normalize(text: str) -> str:
    # 1. Normalize arrow separators to a consistent format
    text = text.replace("==>", "→").replace("-->", "→").replace("=>", "→")
    # 2. Collapse multiple spaces and newlines
    text = re.sub(r'\s+', ' ', text).strip()
    return text
```

After normalization, the example above becomes:
```
Module Hành chính → In → Bảng kê khám bệnh, chữa bệnh
```

### `__main__`

```python
if __name__ == "__main__":
    docs = fetch_all_documents()
    print(f"Total fetched : {len(docs)} documents")
    print(f"Skipped       : (logged above)")
    print(f"\nExample:")
    print(f"  Subject    : {docs[0].subject}")
    print(f"  Description: {docs[0].description}")
    print(f"  URL        : {docs[0].url}")
```

---

## 2. `data/embedder.py`

### Responsibility

Accept a list of `Document` objects, build the text to embed, generate
embeddings with `bge-m3`, and store everything in Qdrant.

### Text to embed — critical decision

Because descriptions average only 123 characters, always concatenate
`subject + description`:

```python
def build_chunk_text(doc: Document) -> str:
    return (
        f"Câu hỏi: {doc.subject}\n"
        f"Hướng dẫn: {doc.description}"
    )
```

Example output:
```
Câu hỏi: in bảng kê khám bệnh, chữa bệnh tìm ở đâu
Hướng dẫn: Module Hành chính → In → Bảng kê khám bệnh, chữa bệnh
```

This combined text gives the embedding model both the question phrasing
(which matches how doctors ask) and the answer content.

### Embedding model

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("BAAI/bge-m3")
```

- Batch size: 32 (safe for V100 16GB)
- Runs on CPU if GPU is reserved for vLLM — bge-m3 is fast enough on CPU
  for a one-time index build

### Qdrant storage

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

client = QdrantClient(url=QDRANT_URL)  # default: http://localhost:6333

# Create collection if it doesn't exist
client.recreate_collection(
    collection_name=QDRANT_COLLECTION,
    vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
    # bge-m3 produces 1024-dim vectors
)

# Upsert points (insert or update — handles re-indexing safely)
client.upsert(
    collection_name=QDRANT_COLLECTION,
    points=[
        PointStruct(
            id=doc.issue_id,
            vector=embedding,
            payload={
                "issue_id": doc.issue_id,
                "subject": doc.subject,
                "description": doc.description,
                "project": doc.project,
                "url": doc.url,
                "chunk_text": chunk_text   # store full text for retrieval
            }
        )
        for doc, embedding, chunk_text in zip(docs, embeddings, chunk_texts)
    ]
)
```

Using `upsert` (not `add`) means re-running the embedder is always safe —
it will update existing entries rather than create duplicates.

### `__main__`

```python
if __name__ == "__main__":
    docs = fetch_all_documents()
    count = embed_and_store(docs)
    print(f"Stored {count} chunks in Qdrant collection '{QDRANT_COLLECTION}'")

    # Sanity check: run a test query
    from core.retriever import retrieve
    results = retrieve("in bảng kê khám bệnh ở đâu", top_k=3)
    print("\nTest query — 'in bảng kê khám bệnh ở đâu':")
    for r in results:
        print(f"  score={r.score:.3f} | {r.metadata['subject']}")
```

---

## 3. `data/reindex.py`

```bash
python -m data.reindex           # drop and rebuild the entire collection
python -m data.reindex --diff    # only update issues modified since last run
                                 # uses updated_on field from Redmine API
```

For `--diff` mode, store the timestamp of the last successful run in a
local file (e.g. `.last_index_time`) and pass it as `updated_after` to
the Redmine API.

---

## Done Criteria

- [ ] Qdrant running: `docker ps` shows qdrant container
- [ ] `python -m data.ingestor` fetches all 5 pages, prints ~455 usable documents
- [ ] `python -m data.embedder` stores all chunks, prints count
- [ ] Running embedder a second time produces no duplicates (upsert behavior)
- [ ] Sanity test query in `__main__` returns relevant results with sensible scores
- [ ] All skipped issues are logged with the reason

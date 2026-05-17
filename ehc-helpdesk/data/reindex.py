"""
Reindex — Drop and rebuild the entire Qdrant collection, or incrementally
update only issues modified since the last run.

Usage:
  python -m data.reindex           # full rebuild
  python -m data.reindex --diff    # incremental update (uses .last_index_time)
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from config import (
    REDMINE_URL, REDMINE_API_KEY, REDMINE_PROJECT,
    QDRANT_URL, QDRANT_COLLECTION, EMBED_MODEL,
)
from data.ingestor import fetch_all_documents, normalize, Document
from data.embedder import build_chunk_text, embed_and_store


LAST_INDEX_FILE = Path(__file__).parent.parent / ".last_index_time"


def full_reindex() -> None:
    """Drop collection and rebuild from scratch."""
    print("[REINDEX] Full rebuild starting...")
    docs = fetch_all_documents()
    count = embed_and_store(docs, recreate=True)
    _save_timestamp()
    print(f"[REINDEX] Full rebuild complete. {count} chunks indexed.")


def diff_reindex() -> None:
    """Only update issues modified since last successful run."""
    last_time = _load_timestamp()
    if not last_time:
        print("[REINDEX] No previous timestamp found. Running full reindex instead.")
        full_reindex()
        return

    print(f"[REINDEX] Incremental update since {last_time}")

    # Fetch issues updated after last_time
    docs: list[Document] = []
    offset = 0
    limit = 100

    while True:
        params = {
            "project_id": REDMINE_PROJECT,
            "limit": limit,
            "offset": offset,
            "key": REDMINE_API_KEY,
            "status_id": "*",
            "updated_on": f">={last_time}",
        }

        response = httpx.get(
            f"{REDMINE_URL}/issues.json",
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        issues = data.get("issues", [])

        if not issues:
            break

        for issue in issues:
            description = issue.get("description", "").strip()
            if not description or len(description) < 20:
                continue

            doc = Document(
                issue_id=issue["id"],
                subject=normalize(issue.get("subject", "").strip()),
                description=normalize(description),
                project=REDMINE_PROJECT,
                url=f"{REDMINE_URL}/issues/{issue['id']}",
            )
            docs.append(doc)

        offset += limit

    if not docs:
        print("[REINDEX] No updated issues found. Collection is up to date.")
        _save_timestamp()
        return

    print(f"[REINDEX] Found {len(docs)} updated issues. Upserting...")

    # Embed and upsert only the changed docs
    model = SentenceTransformer(EMBED_MODEL)
    chunk_texts = [build_chunk_text(doc) for doc in docs]
    embeddings = model.encode(chunk_texts, batch_size=32, show_progress_bar=True)

    client = QdrantClient(url=QDRANT_URL)
    points = [
        PointStruct(
            id=doc.issue_id,
            vector=embedding.tolist(),
            payload={
                "issue_id": doc.issue_id,
                "subject": doc.subject,
                "description": doc.description,
                "project": doc.project,
                "url": doc.url,
                "chunk_text": chunk_text,
            },
        )
        for doc, embedding, chunk_text in zip(docs, embeddings, chunk_texts)
    ]

    client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    _save_timestamp()
    print(f"[REINDEX] Incremental update complete. {len(points)} chunks upserted.")


def _save_timestamp() -> None:
    """Save current UTC timestamp to file."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    LAST_INDEX_FILE.write_text(now)
    print(f"[REINDEX] Saved timestamp: {now}")


def _load_timestamp() -> str | None:
    """Load last index timestamp from file."""
    if LAST_INDEX_FILE.exists():
        return LAST_INDEX_FILE.read_text().strip()
    return None


if __name__ == "__main__":
    if "--diff" in sys.argv:
        diff_reindex()
    else:
        full_reindex()

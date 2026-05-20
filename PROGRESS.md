# EHC Helpdesk — Progress Log

## Phase 0 — Project Scaffold ✅

All skeleton files created, config.py working, .env set up.

## Phase 1 — Data Layer

### data/ingestor.py ✅ (2026-05-13)

```
[INGESTOR] Fetching from http://co.ehc.vn:81/redmine/issues.json (project=ehcfaq)
[INGESTOR] Page 1: fetched 100 issues (offset=0)
  [SKIP] id=42709 subject="x" reason="empty description"
  [SKIP] id=19240 subject="x" reason="empty description"
  [SKIP] id=19066 subject="x" reason="empty description"
[INGESTOR] Page 2: fetched 100 issues (offset=100)
  [SKIP] id=18568 subject="x" reason="too short (1 chars)"
[INGESTOR] Page 3: fetched 100 issues (offset=200)
  [SKIP] id=18339 subject="x" reason="empty description"
  [SKIP] id=18244 subject="Cách tắt đi bật lại app nhanh" reason="too short (9 chars)"
[INGESTOR] Page 4: fetched 100 issues (offset=300)
  [SKIP] id=18111 subject="Hướng dẫn cấu hình chữ ký số" reason="empty description"
  [SKIP] id=18086 subject="Cách cập nhật phần mềm" reason="too short (16 chars)"
  [SKIP] id=18056 subject="Cách cập nhật phần mềm" reason="empty description"
  [SKIP] id=18053 subject="Báo cáo mở rộng là gì" reason="empty description"
[INGESTOR] Page 5: fetched 77 issues (offset=400)
  [SKIP] id=17797 subject="Phần mềm cứ xoay mãi" reason="too short (17 chars)"
  [SKIP] id=17791 subject="Không tạo được phiếu tạm ứng,thu tiền ..." reason="too short (16 chars)"
  [SKIP] id=17737 subject="Bác sĩ không kê được đơn thuốc" reason="too short (18 chars)"

[INGESTOR] Done. Total usable: 464, Skipped: 13

Total fetched : 464 documents
```

Result: **464 documents** fetched (expected ~455, got slightly more — some short descriptions just above 20 char threshold).

### data/embedder.py ✅ (2026-05-13)

```
[EMBEDDER] Built 464 chunk texts
[EMBEDDER] Loading model: BAAI/bge-m3
[EMBEDDER] Encoding 464 texts (batch_size=32)...
[EMBEDDER] Embeddings shape: (464, 1024)
[EMBEDDER] Creating/recreating collection 'ehc_faq' (dim=1024, cosine)
[EMBEDDER] Upserted batch 1: 100 points (total: 100)
[EMBEDDER] Upserted batch 2: 100 points (total: 200)
[EMBEDDER] Upserted batch 3: 100 points (total: 300)
[EMBEDDER] Upserted batch 4: 100 points (total: 400)
[EMBEDDER] Upserted batch 5: 64 points (total: 464)
[EMBEDDER] Done. 464 chunks stored in 'ehc_faq'

--- Sanity Check ---
Test query: 'in bảng kê khám bệnh ở đâu'
  #1 score=0.733 | in bảng kê khám bệnh, chữa bệnh tìm ở đâu
  #2 score=0.676 | In phiếu khám chữa bệnh tìm ở đâu
  #3 score=0.676 | Muốn in sổ khám bệnh ở đâu?
```

Result: **464 chunks** stored in Qdrant. Sanity check query returns highly relevant results (top score 0.733).

### data/reindex.py ✅ (2026-05-13)

```
# Full rebuild:
[REINDEX] Full rebuild starting...
[REINDEX] Full rebuild complete. 464 chunks indexed.
[REINDEX] Saved timestamp: 2026-05-13T14:52:36Z

# Incremental (no changes):
[REINDEX] Incremental update since 2026-05-13T14:52:36Z
[REINDEX] No updated issues found. Collection is up to date.
[REINDEX] Saved timestamp: 2026-05-13T14:52:52Z
```

Result: Both full rebuild and incremental diff modes working correctly.

---

## Phase 1 — COMPLETE ✅

All data layer modules implemented and verified:
- Ingestor: 464 docs fetched from Redmine
- Embedder: 464 chunks stored in Qdrant (bge-m3, 1024-dim, cosine)
- Reindex: full + diff modes working
- Sanity check query returns relevant results (top score 0.733)

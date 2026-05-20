#!/usr/bin/env python3
"""
Fine-tune bge-m3 embedding model on EHC domain data.

Steps:
  1. Generate training pairs: read FAQs from Qdrant, use vLLM to generate
     colloquial Vietnamese questions for each FAQ topic.
  2. Fine-tune bge-m3 with MultipleNegativesRankingLoss (in-batch negatives).
  3. Evaluate: compare cosine similarity before/after on sample pairs.

Usage:
    # Generate training data only (while vLLM is running):
    python scripts/finetune_bge.py --generate-only

    # Full pipeline (generate + fine-tune + evaluate):
    # Run this when vLLM is STOPPED to free GPU/CPU memory.
    python scripts/finetune_bge.py

    # Fine-tune only (skip generation, use existing pairs file):
    python scripts/finetune_bge.py --finetune-only

Dependencies: qdrant-client, sentence-transformers, openai
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer, InputExample, losses
from sentence_transformers.evaluation import EmbeddingSimilarityEvaluator
from torch.utils.data import DataLoader

from config import QDRANT_URL, QDRANT_COLLECTION, EMBED_MODEL, VLLM_BASE_URL, VLLM_MODEL

# --- Paths ---
PROJECT_ROOT = Path(__file__).parent.parent
PAIRS_FILE = PROJECT_ROOT / "data" / "finetune_pairs.jsonl"
OUTPUT_MODEL_DIR = PROJECT_ROOT / "models" / "bge-m3-ehc"
EVAL_SET_FILE = PROJECT_ROOT / "tests" / "eval_set.json"

# --- Training config ---
EPOCHS = 3
BATCH_SIZE = 16
LEARNING_RATE = 2e-5
MAX_SEQ_LENGTH = 512

# Device selection: prefer CUDA if available
import torch
if torch.cuda.is_available():
    DEVICE = "cuda"
    print(f"[CONFIG] GPU detected: {torch.cuda.get_device_name(0)}")
else:
    DEVICE = "cpu"
    print("[CONFIG] WARNING: CUDA not available, falling back to CPU (training will be slow)")

# --- Generation config ---
GENERATION_SYSTEM_PROMPT = (
    "Bạn là bác sĩ Việt Nam đang dùng phần mềm EHC. "
    "Hãy viết 3-5 câu hỏi ngắn, colloquial (như nhắn tin cho đồng nghiệp) về vấn đề sau. "
    "Mỗi câu một dòng, không đánh số.\n"
    "QUAN TRỌNG: Chỉ viết bằng tiếng Việt. Tuyệt đối không dùng tiếng Trung, tiếng Anh hay ngôn ngữ khác."
)


# ============================================================
# Step 1: Generate training data
# ============================================================

def fetch_all_faqs() -> list[dict]:
    """Fetch all FAQ documents from Qdrant collection."""
    print(f"[GENERATE] Connecting to Qdrant at {QDRANT_URL}")
    client = QdrantClient(url=QDRANT_URL)

    # Get collection info to know total points
    info = client.get_collection(QDRANT_COLLECTION)
    total = info.points_count
    print(f"[GENERATE] Collection '{QDRANT_COLLECTION}' has {total} points")

    # Scroll through all points
    faqs = []
    offset = None
    while True:
        results, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in results:
            payload = point.payload
            faqs.append({
                "issue_id": payload.get("issue_id"),
                "subject": payload.get("subject", ""),
                "description": payload.get("description", ""),
                "chunk_text": payload.get("chunk_text", ""),
            })
        if next_offset is None:
            break
        offset = next_offset

    print(f"[GENERATE] Fetched {len(faqs)} FAQs")
    return faqs


def _contains_chinese(text: str) -> bool:
    """Check if text contains Chinese characters (U+4E00–U+9FFF)."""
    return bool(re.search(r'[一-鿿]', text))


def generate_questions_for_faq(client: OpenAI, faq: dict) -> list[str]:
    """Call vLLM to generate colloquial questions for a FAQ topic. Retries up to 3 times."""
    subject = faq["subject"]
    description = faq["description"]

    user_prompt = f"Chủ đề: {subject}\nMô tả: {description}"

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=256,
                temperature=0.3,
            )

            text = response.choices[0].message.content.strip()
            # Split into individual questions, filter empty lines
            questions = [q.strip() for q in text.split("\n") if q.strip()]
            # Remove any numbering artifacts (e.g., "1. ", "- ")
            cleaned = []
            for q in questions:
                q = q.lstrip("0123456789.-) ").strip()
                if q and len(q) > 5:
                    cleaned.append(q)

            # Filter out questions containing Chinese characters
            filtered = [q for q in cleaned if not _contains_chinese(q)]
            removed_count = len(cleaned) - len(filtered)
            if removed_count > 0:
                print(f"  [WARN] Filtered {removed_count} question(s) with Chinese characters for '{subject}'")

            return filtered[:5]  # cap at 5

        except Exception as e:
            if attempt < max_retries:
                print(f"  [RETRY {attempt}/{max_retries}] {subject}")
                time.sleep(2)
            else:
                print(f"  [WARN] Generation failed after {max_retries} retries for '{subject}': {type(e).__name__}: {e}")
                return []


def generate_training_data():
    """Step 1: Generate training pairs from FAQs via vLLM."""
    print("\n" + "=" * 60)
    print("[STEP 1] Generating training data from FAQs")
    print("=" * 60)

    faqs = fetch_all_faqs()
    if not faqs:
        print("[ERROR] No FAQs found in Qdrant. Run data ingestion first.")
        sys.exit(1)

    # Connect to vLLM
    vllm_client = OpenAI(base_url=f"{VLLM_BASE_URL}/v1", api_key="not-needed")

    # Test connection
    try:
        vllm_client.models.list()
        print(f"[GENERATE] vLLM connected at {VLLM_BASE_URL}")
    except Exception as e:
        print(f"[ERROR] Cannot connect to vLLM at {VLLM_BASE_URL}: {e}")
        print("Make sure vLLM is running for data generation.")
        sys.exit(1)

    # Generate pairs
    PAIRS_FILE.parent.mkdir(parents=True, exist_ok=True)
    total_pairs = 0

    with open(PAIRS_FILE, "w", encoding="utf-8") as f:
        for i, faq in enumerate(faqs, 1):
            subject = faq["subject"]
            positive = faq["chunk_text"] or f"Câu hỏi: {subject}\nHướng dẫn: {faq['description']}"

            print(f"  [{i}/{len(faqs)}] {subject}")

            questions = generate_questions_for_faq(vllm_client, faq)

            for q in questions:
                pair = {"query": q, "positive": positive}
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                total_pairs += 1

            if questions:
                print(f"    → {len(questions)} questions generated")
            else:
                print(f"    → SKIPPED (no questions generated)")

            # Small delay to avoid overwhelming vLLM
            time.sleep(0.5)

    print(f"\n[GENERATE] Done. {total_pairs} training pairs saved to {PAIRS_FILE}")
    return total_pairs


# ============================================================
# Step 2: Fine-tune bge-m3
# ============================================================

def load_training_pairs() -> list[dict]:
    """Load training pairs from JSONL file."""
    if not PAIRS_FILE.exists():
        print(f"[ERROR] Training pairs file not found: {PAIRS_FILE}")
        print("Run with --generate-only first to create training data.")
        sys.exit(1)

    pairs = []
    with open(PAIRS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))

    print(f"[FINETUNE] Loaded {len(pairs)} training pairs from {PAIRS_FILE}")
    return pairs


def finetune():
    """Step 2: Fine-tune bge-m3 with MultipleNegativesRankingLoss."""
    print("\n" + "=" * 60)
    print("[STEP 2] Fine-tuning bge-m3")
    print("=" * 60)

    pairs = load_training_pairs()
    if len(pairs) < BATCH_SIZE:
        print(f"[WARN] Only {len(pairs)} pairs — batch_size reduced to {len(pairs)}")
        batch_size = max(2, len(pairs))
    else:
        batch_size = BATCH_SIZE

    # Load base model
    print(f"[FINETUNE] Loading base model: {EMBED_MODEL}")
    print(f"[FINETUNE] Device: {DEVICE}")
    model = SentenceTransformer(EMBED_MODEL, device=DEVICE)
    model.max_seq_length = MAX_SEQ_LENGTH

    # Create training examples (query, positive) — in-batch negatives are automatic
    train_examples = [
        InputExample(texts=[p["query"], p["positive"]])
        for p in pairs
    ]
    print(f"[FINETUNE] Training examples: {len(train_examples)}")

    # DataLoader
    train_dataloader = DataLoader(
        train_examples, shuffle=True, batch_size=batch_size
    )

    # Loss: MultipleNegativesRankingLoss uses other positives in the batch as negatives
    train_loss = losses.MultipleNegativesRankingLoss(model=model)

    # Training
    warmup_steps = int(len(train_dataloader) * EPOCHS * 0.1)
    print(f"[FINETUNE] Config:")
    print(f"  epochs         : {EPOCHS}")
    print(f"  batch_size     : {batch_size}")
    print(f"  learning_rate  : {LEARNING_RATE}")
    print(f"  max_seq_length : {MAX_SEQ_LENGTH}")
    print(f"  warmup_steps   : {warmup_steps}")
    print(f"  output_dir     : {OUTPUT_MODEL_DIR}")
    print()

    OUTPUT_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=EPOCHS,
        warmup_steps=warmup_steps,
        optimizer_params={"lr": LEARNING_RATE},
        output_path=str(OUTPUT_MODEL_DIR),
        show_progress_bar=True,
    )

    print(f"\n[FINETUNE] Model saved to {OUTPUT_MODEL_DIR}")


# ============================================================
# Step 3: Evaluate
# ============================================================

def evaluate():
    """Step 3: Compare cosine similarity before/after fine-tuning."""
    print("\n" + "=" * 60)
    print("[STEP 3] Evaluation — before vs after fine-tuning")
    print("=" * 60)

    if not OUTPUT_MODEL_DIR.exists():
        print(f"[ERROR] Fine-tuned model not found at {OUTPUT_MODEL_DIR}")
        return

    # Load sample pairs for evaluation
    sample_pairs = _get_eval_pairs()
    if not sample_pairs:
        print("[WARN] No evaluation pairs available, skipping evaluation.")
        return

    # Load both models
    print(f"[EVAL] Loading base model: {EMBED_MODEL}")
    base_model = SentenceTransformer(EMBED_MODEL, device=DEVICE)

    print(f"[EVAL] Loading fine-tuned model: {OUTPUT_MODEL_DIR}")
    ft_model = SentenceTransformer(str(OUTPUT_MODEL_DIR), device=DEVICE)

    # Compare
    print(f"\n{'Query':<45} {'Base':>6} {'FT':>6} {'Δ':>6}")
    print("-" * 70)

    import numpy as np

    for query, positive in sample_pairs[:5]:
        # Base model
        q_emb_base = base_model.encode(query)
        p_emb_base = base_model.encode(positive)
        cos_base = float(np.dot(q_emb_base, p_emb_base) / (
            np.linalg.norm(q_emb_base) * np.linalg.norm(p_emb_base)
        ))

        # Fine-tuned model
        q_emb_ft = ft_model.encode(query)
        p_emb_ft = ft_model.encode(positive)
        cos_ft = float(np.dot(q_emb_ft, p_emb_ft) / (
            np.linalg.norm(q_emb_ft) * np.linalg.norm(p_emb_ft)
        ))

        delta = cos_ft - cos_base
        query_short = query[:43] if len(query) > 43 else query
        print(f"{query_short:<45} {cos_base:>6.4f} {cos_ft:>6.4f} {delta:>+6.4f}")

    # Instructions
    print("\n" + "=" * 60)
    print("[NEXT STEPS] To use the fine-tuned model:")
    print("=" * 60)
    print(f"""
1. Update config.py:
   Change EMBED_MODEL to point to the local fine-tuned model:
     EMBED_MODEL = _get("EMBED_MODEL", "{OUTPUT_MODEL_DIR}")

2. Or set in .env:
     EMBED_MODEL={OUTPUT_MODEL_DIR}

3. Re-embed all FAQs with the new model:
     python -m data.embedder

   This will re-encode all documents with the fine-tuned embeddings
   and update Qdrant.

4. Both core/retriever.py and data/embedder.py read EMBED_MODEL from
   config.py — no code changes needed, just update the config value.

Note: The fine-tuned model is at: {OUTPUT_MODEL_DIR}
""")


def _get_eval_pairs() -> list[tuple[str, str]]:
    """Get evaluation pairs from eval_set.json or training data."""
    # Try eval_set.json first
    if EVAL_SET_FILE.exists():
        with open(EVAL_SET_FILE, "r", encoding="utf-8") as f:
            eval_data = json.load(f)

        pairs = []
        for item in eval_data:
            if item.get("expected_faq_subject"):
                pairs.append((
                    item["question"],
                    f"Câu hỏi: {item['expected_faq_subject']}"
                ))
        if pairs:
            return pairs[:5]

    # Fallback: use first 5 training pairs
    if PAIRS_FILE.exists():
        pairs = []
        with open(PAIRS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    p = json.loads(line.strip())
                    pairs.append((p["query"], p["positive"]))
                    if len(pairs) >= 5:
                        break
        return pairs

    return []


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fine-tune bge-m3 on EHC domain data")
    parser.add_argument(
        "--generate-only", action="store_true",
        help="Only generate training data (Step 1). Use while vLLM is running."
    )
    parser.add_argument(
        "--finetune-only", action="store_true",
        help="Only fine-tune (Step 2+3). Use existing pairs file."
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  EHC bge-m3 Fine-tuning Script")
    print("=" * 60)

    if args.generate_only:
        generate_training_data()
        print("\n[DONE] Training data generated. Run without --generate-only to fine-tune.")
        print("       (Stop vLLM first to free memory for fine-tuning on CPU)")
    elif args.finetune_only:
        finetune()
        evaluate()
    else:
        generate_training_data()
        finetune()
        evaluate()


if __name__ == "__main__":
    main()

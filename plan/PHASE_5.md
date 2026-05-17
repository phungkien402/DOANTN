# PHASE 5 — RAG Quality Evaluation

## Goal

Get concrete numbers: what percentage of questions does the system answer
correctly, where does it fail, and what needs to improve. Without this phase,
every improvement is guesswork.

```bash
python -m tests.evaluate
# prints a results table and overall score
```

## Prerequisites

- Phase 2 (RAG Core) complete
- Phases 3 and 4 do not need to be complete — evaluation runs directly
  against the pipeline

---

## 1. Evaluation set: `tests/eval_set.json`

Build a set of at least **20 real questions** sourced from the helpdesk team,
covering four categories:

- **10** questions that have a clear answer in the FAQ
- **5** colloquial/shorthand questions (to test the Query Rewriter)
- **3** questions with no matching FAQ entry (to test Fallback)
- **2** genuinely ambiguous short questions (to test clarification behavior)

```json
[
  {
    "id": "q01",
    "question": "how do I merge duplicate patient records",
    "type": "in_faq",
    "expected_keywords": ["merge", "patient", "step", "administration"],
    "expected_faq_subject": "Merge duplicate patient records"
  },
  {
    "id": "q02",
    "question": "print daily medication order sheet",
    "type": "in_faq",
    "expected_keywords": ["print", "medication", "order", "menu"],
    "expected_faq_subject": null
  },
  {
    "id": "q03",
    "question": "record locked what do",
    "type": "in_faq",
    "expected_keywords": ["locked", "record", "unlock"],
    "expected_faq_subject": null
  },
  {
    "id": "q04",
    "question": "huh??",
    "type": "ambiguous",
    "expected_behavior": "ask_clarification"
  },
  {
    "id": "q05",
    "question": "how to configure proxy server settings",
    "type": "not_in_faq",
    "expected_behavior": "fallback"
  }
]
```

---

## 2. `tests/evaluate.py`

### Scoring logic

```python
for item in eval_set:
    msg = Message(
        user_id="eval", session_id=item["id"],
        text=item["question"], timestamp=time.time(), platform="web"
    )
    answer = pipeline.run(msg, [])

    if item["type"] == "in_faq":
        # Pass if: not a fallback AND response contains all expected keywords
        passed = (
            not answer.is_fallback
            and all(
                kw.lower() in answer.text.lower()
                for kw in item["expected_keywords"]
            )
        )

    elif item["type"] == "not_in_faq":
        # Pass if: is_fallback is True
        passed = answer.is_fallback

    elif item["type"] == "ambiguous":
        # Pass if: response is a clarifying question
        passed = "?" in answer.text or "describe" in answer.text.lower()
```

### Output format

```
=== EHC RAG — Evaluation Results ===

ID    Question                           Type        Pass  Confidence
----- ---------------------------------- ----------- ----- ----------
q01   how do I merge duplicate patient   in_faq      ✅    0.94
q02   print daily medication order       in_faq      ✅    0.81
q03   record locked what do              in_faq      ❌    0.38   ← FAIL
q04   huh??                              ambiguous   ✅    —
q05   configure proxy server             not_in_faq  ✅    0.09

Overall   : 4 / 5 passed  (80.0%)
In-FAQ    : 2 / 3 (66.7%)
Fallback  : 1 / 1 (100%)
Ambiguous : 1 / 1 (100%)

=== NEEDS ATTENTION ===
q03 — confidence 0.38 is below threshold 0.40
  → Review the FAQ entry for "locked record" — description may be too vague
  → Or lower CONFIDENCE_THRESHOLD to 0.35 and re-run to see effect
```

---

## 3. Debug tool: `tests/debug_query.py`

A script to trace a single question through the entire pipeline — the most
useful tool when investigating a failing eval case:

```bash
python -m tests.debug_query "record locked what do"
```

Output:

```
=== DEBUG: "record locked what do" ===

[REWRITER]
  Original : "record locked what do"
  Rewritten: "What should I do when a patient record is locked in EHC?"

[RETRIEVER] Top 10 chunks:
  #1  sim=0.71 | "Delete patient record: Go to patient list..."
  #2  sim=0.68 | "Lock record on discharge: After the patient..."
  #3  sim=0.65 | "Unlock record: Contact your administrator..."
  ...

[RERANKER] Top 3 after reranking:
  #1  score=0.38 | "Unlock record: Contact your administrator..."   ← TOP
  #2  score=0.31 | "Lock record on discharge: After the patient..."
  #3  score=0.18 | "Delete patient record: Go to patient list..."

[CONFIDENCE] 0.38 < 0.40 threshold → FALLBACK

[DIAGNOSIS]
  The correct chunk ("Unlock record") was retrieved but scored below threshold.
  Possible fixes:
    1. Expand the FAQ entry description — add more context and keywords.
    2. Lower CONFIDENCE_THRESHOLD to 0.35 and re-evaluate.
    3. Add a synonym mapping: "locked" → "unlock" in the rewriter prompt.
```

---

## 4. Deployment readiness criteria

The system is ready for internal rollout when all of the following are met:

| Metric | Minimum threshold |
|---|---|
| In-FAQ accuracy | ≥ 80% |
| Fallback accuracy | ≥ 95% |
| Hallucination rate | 0% (manual spot-check) |
| Response time | < 10 seconds per question |
| Eval set size | ≥ 20 questions |

---

## Done Criteria

- [ ] `python -m tests.evaluate` runs and prints the results table
- [ ] `python -m tests.debug_query "..."` shows full per-step trace
- [ ] In-FAQ accuracy ≥ 80% on the eval set
- [ ] No hallucinated answers (manually verify each "in_faq" pass)
- [ ] Eval set contains ≥ 20 questions sourced from real helpdesk history
- [ ] At least one failing case has been diagnosed and improved using
      `debug_query` before marking this phase complete

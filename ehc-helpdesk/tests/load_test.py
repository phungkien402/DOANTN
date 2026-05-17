"""
Load test — sends concurrent requests to POST /webhook/web
and reports throughput, latency percentiles, and error rate.

Usage:
    python -m tests.load_test                    # default: 20 users, 5 requests each
    python -m tests.load_test --users 50 --requests 10
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

# Load questions from eval_set.json (first 10)
_EVAL_SET_PATH = Path(__file__).parent / "eval_set.json"
with open(_EVAL_SET_PATH) as f:
    _ALL_QUESTIONS = [item["question"] for item in json.load(f)]
QUESTIONS = _ALL_QUESTIONS[:10]

TARGET_URL = "http://localhost:8080/webhook/web"


async def worker(
    worker_id: int,
    num_requests: int,
    results: list[dict],
) -> None:
    """Simulate a single user sending sequential requests."""
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(num_requests):
            question = QUESTIONS[(worker_id * num_requests + i) % len(QUESTIONS)]
            payload = {
                "user_id": f"load_test_{worker_id}",
                "session_id": f"s_{worker_id}",
                "text": question,
            }

            start = time.perf_counter()
            try:
                resp = await client.post(TARGET_URL, json=payload)
                elapsed = time.perf_counter() - start
                results.append({
                    "worker": worker_id,
                    "request": i,
                    "status": resp.status_code,
                    "latency": elapsed,
                    "error": None if resp.status_code == 200 else resp.text[:100],
                })
            except Exception as e:
                elapsed = time.perf_counter() - start
                results.append({
                    "worker": worker_id,
                    "request": i,
                    "status": 0,
                    "latency": elapsed,
                    "error": str(e)[:100],
                })


def percentile(data: list[float], p: int) -> float:
    """Calculate the p-th percentile of a sorted list."""
    if not data:
        return 0.0
    k = (len(data) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(data) else f
    d = k - f
    return data[f] + d * (data[c] - data[f])


def print_summary(results: list[dict], total_time: float, num_users: int, num_requests: int) -> None:
    """Print a clean summary table."""
    total_reqs = len(results)
    errors = [r for r in results if r["status"] != 200]
    error_rate = len(errors) / total_reqs * 100 if total_reqs else 0

    latencies = sorted([r["latency"] for r in results])
    p50 = percentile(latencies, 50)
    p90 = percentile(latencies, 90)
    p95 = percentile(latencies, 95)
    avg = statistics.mean(latencies) if latencies else 0
    rps = total_reqs / total_time if total_time > 0 else 0

    print("\n" + "=" * 60)
    print("  LOAD TEST RESULTS")
    print("=" * 60)
    print(f"  Target URL       : {TARGET_URL}")
    print(f"  Concurrent users : {num_users}")
    print(f"  Requests/user    : {num_requests}")
    print(f"  Total requests   : {total_reqs}")
    print("-" * 60)
    print(f"  Total time       : {total_time:.2f}s")
    print(f"  Throughput       : {rps:.2f} req/s")
    print("-" * 60)
    print(f"  Avg latency      : {avg:.3f}s")
    print(f"  P50 latency      : {p50:.3f}s")
    print(f"  P90 latency      : {p90:.3f}s")
    print(f"  P95 latency      : {p95:.3f}s")
    print(f"  Min latency      : {latencies[0]:.3f}s" if latencies else "")
    print(f"  Max latency      : {latencies[-1]:.3f}s" if latencies else "")
    print("-" * 60)
    print(f"  Errors           : {len(errors)}/{total_reqs} ({error_rate:.1f}%)")
    print("=" * 60)

    if errors:
        print("\n  Sample errors:")
        for e in errors[:5]:
            print(f"    worker={e['worker']} req={e['request']} status={e['status']} err={e['error']}")


async def main(num_users: int, num_requests: int) -> None:
    print(f"[LOAD TEST] Starting: {num_users} users × {num_requests} requests = {num_users * num_requests} total")
    print(f"[LOAD TEST] Target: {TARGET_URL}")
    print(f"[LOAD TEST] Questions pool: {len(QUESTIONS)} questions")
    print()

    results: list[dict] = []

    start = time.perf_counter()
    tasks = [worker(i, num_requests, results) for i in range(num_users)]
    await asyncio.gather(*tasks)
    total_time = time.perf_counter() - start

    print_summary(results, total_time, num_users, num_requests)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load test for EHC Helpdesk")
    parser.add_argument("--users", type=int, default=20, help="Number of concurrent users (default: 20)")
    parser.add_argument("--requests", type=int, default=5, help="Requests per user (default: 5)")
    parser.add_argument("--url", type=str, default=None, help="Target URL override")
    args = parser.parse_args()

    if args.url:
        TARGET_URL = args.url

    asyncio.run(main(args.users, args.requests))

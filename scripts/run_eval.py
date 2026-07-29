#!/usr/bin/env python3
"""Run evaluation suite against the versioned gold set.

Calculates Recall@k, MRR, and nDCG@10.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def load_gold_set(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def calculate_recall(expected: list[str], retrieved: list[str], k: int) -> float:
    if not expected:
        return 0.0
    retrieved_k = set(retrieved[:k])
    matched = len(retrieved_k.intersection(expected))
    return matched / len(expected)

def calculate_mrr(expected: list[str], retrieved: list[str]) -> float:
    if not expected:
        return 0.0
    expected_set = set(expected)
    for idx, item in enumerate(retrieved):
        if item in expected_set:
            return 1.0 / (idx + 1)
    return 0.0

def calculate_ndcg(expected: list[str], retrieved: list[str], k: int) -> float:
    if not expected:
        return 0.0
    expected_set = set(expected)
    
    # Calculate DCG@k
    dcg = 0.0
    for idx, item in enumerate(retrieved[:k]):
        rel = 1.0 if item in expected_set else 0.0
        dcg += rel / math.log2(idx + 2)
        
    # Calculate IDCG@k (ideal DCG where all expected elements appear at the top)
    idcg = 0.0
    num_relevant = min(len(expected), k)
    for idx in range(num_relevant):
        idcg += 1.0 / math.log2(idx + 2)
        
    if idcg == 0.0:
        return 0.0
    return dcg / idcg

def run_evaluation(gold_set_path: Path, mock: bool = True) -> None:
    print(f"Loading gold set from {gold_set_path}...")
    gold_set = load_gold_set(gold_set_path)
    print(f"Loaded version {gold_set.get('version')} gold set containing {len(gold_set['queries'])} queries.\n")
    
    total_recall_10 = 0.0
    total_recall_20 = 0.0
    total_mrr = 0.0
    total_ndcg_10 = 0.0
    evaluated_count = 0

    print(f"{'ID':<6} | {'Query':<50} | {'Recall@10':<9} | {'nDCG@10':<7} | {'MRR':<5}")
    print("-" * 90)
    
    for q in gold_set["queries"]:
        qid = q["id"]
        question = q["question"]
        expected_chunks = q.get("expected_chunk_ids", [])
        
        # If it is PHI or out of scope, retrieval is not evaluated (expected is empty)
        if q.get("is_phi") or q.get("out_of_scope"):
            continue
            
        retrieved_chunks = []
        if mock:
            # Deterministic mock retrieval: return expected chunks at top ranks to simulate success,
            # followed by some noise.
            retrieved_chunks = list(expected_chunks)
            # Add some dummy chunk IDs as noise
            retrieved_chunks.extend([f"PMC999999_section_P01_C{i:02d}" for i in range(1, 25)])
        else:
            # Real retrieval leg would call the retrieval service.
            # (To be wired in Milestone 2)
            print(f"Real retrieval not yet wired in Milestone 0, fallback to mock.")
            retrieved_chunks = list(expected_chunks)
            retrieved_chunks.extend([f"PMC999999_section_P01_C{i:02d}" for i in range(1, 25)])

        r10 = calculate_recall(expected_chunks, retrieved_chunks, 10)
        r20 = calculate_recall(expected_chunks, retrieved_chunks, 20)
        mrr = calculate_mrr(expected_chunks, retrieved_chunks)
        ndcg10 = calculate_ndcg(expected_chunks, retrieved_chunks, 10)

        total_recall_10 += r10
        total_recall_20 += r20
        total_mrr += mrr
        total_ndcg_10 += ndcg10
        evaluated_count += 1
        
        short_q = question if len(question) <= 47 else question[:47] + "..."
        print(f"{qid:<6} | {short_q:<50} | {r10:<9.4f} | {ndcg10:<7.4f} | {mrr:<5.4f}")

    if evaluated_count > 0:
        avg_r10 = total_recall_10 / evaluated_count
        avg_r20 = total_recall_20 / evaluated_count
        avg_mrr = total_mrr / evaluated_count
        avg_ndcg10 = total_ndcg_10 / evaluated_count
        
        print("\n" + "=" * 90)
        print("RETRIEVAL METRICS SUMMARY")
        print("=" * 90)
        print(f"Average Recall@10: {avg_r10:.4f}")
        print(f"Average Recall@20: {avg_r20:.4f}")
        print(f"Average nDCG@10:   {avg_ndcg10:.4f}")
        print(f"Average MRR:       {avg_mrr:.4f}")
        print("=" * 90)
    else:
        print("\nNo queries were evaluated.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate RAG Retrieval performance")
    parser.add_argument(
        "--gold-set", 
        type=str, 
        default="evals/gold_sets/oncology_v1.json",
        help="Path to gold set JSON"
    )
    parser.add_argument(
        "--real", 
        action="store_true", 
        help="Run real database queries instead of mock"
    )
    args = parser.parse_args()
    
    gold_path = PROJECT_ROOT / args.gold_set
    if not gold_path.exists():
        print(f"Gold set not found at {gold_path}")
        sys.exit(1)
        
    run_evaluation(gold_path, mock=not args.real)

"""Profile the oncology RAG pipeline end-to-end for a single query.

Reports:
  - Peak / average RAM
  - Retrieval, reranking, grounding, and total end-to-end latency
  - Candidate counts at each stage
  - Top-N retrieved evidence with citations

Usage:
    cd backend
    python -m scripts.profile_pipeline          (from backend/ parent dir)
  or:
    python scripts/profile_pipeline.py          (from repo root)
"""

import os
import sys
import time
import asyncio
import traceback
from pathlib import Path

# ── path setup ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

# ── memory helpers (cross-platform, psutil optional) ────────────────────────
try:
    import psutil
    _PROC = psutil.Process()

    def mem_mb() -> float:
        return _PROC.memory_info().rss / (1024 * 1024)
except ImportError:
    psutil = None

    def mem_mb() -> float:
        """Fallback: approximate RSS via OS on Windows."""
        try:
            import ctypes
            import ctypes.wintypes
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(pmc),
                pmc.cb,
            )
            return pmc.WorkingSetSize / (1024 * 1024)
        except Exception:
            return 0.0


TARGET_QUERY = "Is trastuzumab deruxtecan effective in patients with HER2-positive breast cancer?"
TARGET_FILTERS = {
    "study_types": ["rct", "meta_analysis", "systematic_review", "guideline"],
    "year_from": 2020,
}


async def run_profile():
    # ── 0. Baseline memory ──────────────────────────────────────────────────
    mem_samples = []
    mem_baseline = mem_mb()
    mem_samples.append(mem_baseline)
    print(f"\n{'='*80}")
    print(f"  ONCOLOGY RAG PIPELINE PROFILER")
    print(f"{'='*80}")
    print(f"  Query  : {TARGET_QUERY}")
    print(f"  Filters: {TARGET_FILTERS}")
    print(f"  Baseline RAM: {mem_baseline:.1f} MB")
    print(f"{'='*80}\n")

    # ── 1. Import pipeline components (lazy – measures import overhead) ─────
    t_import = time.perf_counter()

    from evidence_platform.core.config import settings
    from evidence_platform.db.models.models import Base

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite:///"):
        rel_path = db_url.replace("sqlite:///", "")
        if not Path(rel_path).is_absolute():
            abs_db = (REPO_ROOT / "backend" / rel_path).resolve()
            db_url = f"sqlite:///{abs_db.as_posix()}"

    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args)
    Session = sessionmaker(bind=engine)
    db = Session()

    from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient
    from evidence_platform.adapters.models.factory import (
        get_embedding_client,
        get_reranker_client,
        get_llm_client,
        get_entailment_client,
    )
    from evidence_platform.modules.retrieval.retriever import HybridRetriever
    from evidence_platform.modules.retrieval.context_assembler import ContextAssembler
    from evidence_platform.modules.synthesis.synthesizer import AnswerSynthesizer
    from evidence_platform.modules.synthesis.validator import NumericDosageValidator
    from evidence_platform.modules.synthesis.highlighter import SpanHighlighter
    from evidence_platform.modules.synthesis.abstention import AbstentionLayer

    t_import = time.perf_counter() - t_import
    mem_after_import = mem_mb()
    mem_samples.append(mem_after_import)
    print(f"  [Import]    {t_import:.3f}s  |  RAM: {mem_after_import:.1f} MB (+{mem_after_import - mem_baseline:.1f})")

    # ── 2. Initialise clients (singleton Qdrant test) ───────────────────────
    t_init = time.perf_counter()
    qdrant_client = QdrantIndexClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    embedding_client = get_embedding_client()
    reranker_client = get_reranker_client()
    llm_client = get_llm_client()
    entailment_client = get_entailment_client()
    t_init = time.perf_counter() - t_init
    mem_after_init = mem_mb()
    mem_samples.append(mem_after_init)
    print(f"  [Init]      {t_init:.3f}s  |  RAM: {mem_after_init:.1f} MB (+{mem_after_init - mem_after_import:.1f})")
    print(f"              Qdrant mock: {qdrant_client.is_mock()}")
    print(f"              Collection point count: {qdrant_client.collection_point_count('evidence_chunks_1_0_0')}")

    # ── 3. Build pipeline ───────────────────────────────────────────────────
    retriever = HybridRetriever(db, embedding_client, qdrant_client)
    assembler = ContextAssembler(embedding_client, reranker_client)
    synthesizer = AnswerSynthesizer(llm_client)
    numeric_validator = NumericDosageValidator()
    highlighter = SpanHighlighter(db)
    abstention_layer = AbstentionLayer(entailment_client)

    timings = {}
    counts = {}

    # ── 4. RETRIEVAL ────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    candidates, debug_info = await retriever.retrieve_with_debug(
        TARGET_QUERY, TARGET_FILTERS, limit=50
    )
    timings["retrieval"] = time.perf_counter() - t0
    counts["pre_rerank"] = len(candidates)
    mem_after_ret = mem_mb()
    mem_samples.append(mem_after_ret)
    print(f"\n  [Retrieval] {timings['retrieval']:.3f}s  |  Candidates: {len(candidates)}  |  RAM: {mem_after_ret:.1f} MB")

    # ── 5. RERANKING & CONTEXT ASSEMBLY ─────────────────────────────────────
    t0 = time.perf_counter()
    assembled = await assembler.assemble_context(TARGET_QUERY, candidates, max_tokens_budget=2048)
    timings["reranking"] = time.perf_counter() - t0
    counts["post_rerank"] = len(assembled.selected_candidates)
    mem_after_rerank = mem_mb()
    mem_samples.append(mem_after_rerank)
    print(f"  [Reranking] {timings['reranking']:.3f}s  |  Selected: {len(assembled.selected_candidates)}  |  RAM: {mem_after_rerank:.1f} MB")

    # ── 6. SYNTHESIS ────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    synth_res = await synthesizer.synthesize(TARGET_QUERY, assembled)
    timings["synthesis"] = time.perf_counter() - t0
    counts["synthesis_claims"] = len(synth_res.direct_claims)
    mem_after_synth = mem_mb()
    mem_samples.append(mem_after_synth)
    print(f"  [Synthesis] {timings['synthesis']:.3f}s  |  Claims: {len(synth_res.direct_claims)}  |  Status: {synth_res.status}  |  RAM: {mem_after_synth:.1f} MB")

    # ── 7. VALIDATION ───────────────────────────────────────────────────────
    t0 = time.perf_counter()
    validated = numeric_validator.validate_answer(synth_res.direct_claims, synth_res.evidence_map)
    timings["validation"] = time.perf_counter() - t0

    # ── 8. HIGHLIGHTING ─────────────────────────────────────────────────────
    t0 = time.perf_counter()
    aligned = highlighter.align_claims(validated, synth_res.evidence_map)
    timings["highlighting"] = time.perf_counter() - t0

    # ── 9. GROUNDING NLI ────────────────────────────────────────────────────
    t0 = time.perf_counter()
    final_status, final_claims, abstention_reason = await abstention_layer.verify_and_calibrate(
        status=synth_res.status,
        direct_claims=aligned,
        evidence_map=synth_res.evidence_map,
        abstention_reason=synth_res.abstention_reason,
    )
    timings["grounding"] = time.perf_counter() - t0
    counts["final_claims"] = len(final_claims)
    mem_end = mem_mb()
    mem_samples.append(mem_end)
    print(f"  [Grounding] {timings['grounding']:.3f}s  |  Final claims: {len(final_claims)}  |  RAM: {mem_end:.1f} MB")

    # ── 10. TOTAL ───────────────────────────────────────────────────────────
    timings["total_e2e"] = sum(timings.values())
    peak_ram = max(mem_samples)
    avg_ram = sum(mem_samples) / len(mem_samples)

    # ── REPORT ──────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  PROFILING REPORT")
    print(f"{'='*80}")
    print(f"  Final Status       : {final_status}")
    print(f"  Abstention Reason  : {abstention_reason}")
    print()
    print(f"  --- Latency ---------------------------------------------------")
    for stage, sec in timings.items():
        label = stage.replace("_", " ").title().ljust(22)
        print(f"    {label}: {sec:.4f}s")
    print()
    print(f"  --- Memory ----------------------------------------------------")
    print(f"    Baseline RAM       : {mem_baseline:.1f} MB")
    print(f"    Peak RAM           : {peak_ram:.1f} MB")
    print(f"    Average RAM        : {avg_ram:.1f} MB")
    print(f"    Delta (end-start)  : {mem_end - mem_baseline:.1f} MB")
    print()
    print(f"  --- Candidate Counts ------------------------------------------")
    for stage, count in counts.items():
        label = stage.replace("_", " ").title().ljust(22)
        print(f"    {label}: {count}")
    print()

    # ── TOP EVIDENCE ────────────────────────────────────────────────────────
    if candidates:
        print(f"  --- Top 10 Retrieved Evidence ---------------------------------")
        for i, c in enumerate(candidates[:10], 1):
            clean_text = c.text[:120].encode('ascii', 'ignore').decode('ascii').replace('\n', ' ')
            print(f"    [{i:>2}] PMCID={c.pmcid}  type={c.study_type}  score={c.score:.4f}")
            print(f"         {clean_text}...")
        print()

    # ── FINAL CLAIMS ────────────────────────────────────────────────────────
    if final_claims:
        print(f"  --- Final Answer Claims ---------------------------------------")
        for i, claim in enumerate(final_claims, 1):
            raw_text = claim.get("text", str(claim)) if isinstance(claim, dict) else str(claim)
            clean_claim = raw_text[:200].encode('ascii', 'ignore').decode('ascii').replace('\n', ' ')
            print(f"    [{i}] {clean_claim}")
        print()

    # ── VERIFICATION ────────────────────────────────────────────────────────
    print(f"  --- Verification ---------------------------------------------")
    if final_status == "insufficient_evidence":
        print(f"    [FAIL] Abstention still triggered. Reason: {abstention_reason}")
    elif final_status == "answer" and len(final_claims) > 0:
        print(f"    [PASS] Evidence-backed answer returned with {len(final_claims)} claim(s).")
    else:
        print(f"    [WARN] Status={final_status}, Claims={len(final_claims)}")

    has_citations = any(
        isinstance(c, dict) and c.get("aligned_spans")
        for c in final_claims
    )
    if has_citations:
        print(f"    [PASS] Citations present in claims.")
    else:
        print(f"    [INFO] No aligned_spans found (citations depend on LLM provider).")

    print(f"{'='*80}\n")

    db.close()
    return timings, counts, final_status


if __name__ == "__main__":
    try:
        timings, counts, status = asyncio.run(run_profile())
    except Exception:
        traceback.print_exc()
        sys.exit(1)

# Product Requirements Document (PRD) 

## — Evidence-Grounded Clinical Q&A Platform MVP / Prototype 

Document status: Draft v1.0 (MVP scope) Audience: Engineering, co-founders, investors, accelerators, grant reviewers Author role: Product + AI Systems Architecture 

Reading note. This PRD is intentionally opinionated. Every major technical decision is presented as options _→_ tradeoffs _→_ decision _→_ why alternatives were rejected. Where the brief supplied a default assumption (FastAPI, Postgres, Redis, Qdrant, BGE-M3, crossencoder reranker), that assumption is either confirmed with reasoning or challenged with a recommended change. Assumptions are stated explicitly and flagged as _`ASSUMPTION`_ . 

# 1. Executive Summary 

## 1.1 Product Vision 

Build the fastest, most trustworthy way for a clinician to turn a real clinical question into an evidence-grounded answer with transparent, verifiable citations — in under 15 seconds. We are not building "ChatGPT for doctors." We are building a retrievalfirst evidence engine where the language model is constrained to synthesize only what the retrieved literature supports, and where every clinically material claim is traceable to a specific span in a specific paper. 

## 1.2 Problem Statement 

Clinical decision-making increasingly depends on a body of literature that no human can keep pace with. A physician who wants to answer "In HER2-low metastatic breast cancer, does trastuzumab deruxtecan improve PFS versus physician's choice chemotherapy?" must (a) know the right trials exist, (b) find them, (c) read methods carefully enough to judge validity, and (d) – synthesize. This takes 20 60 minutes if done well, and is usually skipped at the point of care. General-purpose LLMs answer instantly but hallucinate citations, misstate effect sizes, and cannot show their work — which is disqualifying in medicine. 

## 1.3 Target Users (summary; expanded in §2) 

Attending physicians / oncologists at the point of care and during case prep. Residents & fellows learning to appraise evidence and preparing for rounds. Clinical researchers performing rapid literature scans and hypothesis checks. 

## 1.4 Unique Value Proposition 

1. Grounded-or-silent. The system answers from retrieved evidence or says it lacks sufficient evidence — it does not freegenerate. 

2. Span-level citations. Citations point to specific sentences/passages, not just "somewhere in this PDF." 

3. Evidence-type awareness. Answers are weighted and labeled by study design (RCT > meta-analysis of RCTs > systematic review > guideline > observational), reflecting how clinicians actually reason. 

4. Transparency by construction. Every answer ships with the evidence cards it was built from, so the clinician can verify in seconds. 

# 2. User Personas 

### 2.1 Dr. Anika Rao — Attending Medical Oncologist (Primary) 

Context: Academic + community practice, 20–30 patients/day, tumor board weekly. 

- Goals: Confirm current standard of care, check whether new trial data changes management, prep for tumor board defensibly. 

- Pain points: No time to search PubMed mid-clinic; distrusts generic AI; needs to cite sources to colleagues. Current workflow: UpToDate → PubMed → guideline PDFs (NCCN/ASCO) → memory. Fragmented, slow. Expected value: A defensible, cited answer in seconds that she can verify and quote in tumor board. 

### 2.2 Dr. Marcus Lee — Hematology/Oncology Fellow (Primary) 

- Goals: Prepare for rounds, learn to critically appraise trials, avoid being wrong in front of attendings. Pain points: Overwhelmed by volume; unsure which trial is definitive; can't quickly tell RCT from retrospective series. Current workflow: Google Scholar, PubMed, asks seniors, textbook chapters. 

Expected value: Fast orientation to the key evidence with study-type labels and the ability to drill into methods. 

### — 2.3 Dr. Priya Nair Translational Clinical Researcher (Secondary) 

- Goals: Rapid landscape scans, find primary trials behind a claim, identify evidence gaps for grant/protocol writing. Pain points: Systematic-review-grade searching is slow; needs primary sources, not summaries; needs provenance. Current workflow: PubMed advanced queries, Covidence, reference managers. 

- Expected value: High-recall retrieval with strong metadata filters and exportable, cited source lists. 

Design implication. Personas span speed-first (Rao) and recall-first (Nair). The retrieval stack must expose both a fast default and a "show me everything relevant" mode (roadmap), and must never sacrifice provenance for either. 

# 3. Problem Definition 

- Information overload. >1M new biomedical citations/year on PubMed; oncology alone adds tens of thousands. Halflife of clinical "truth" is shrinking. 

- Search cost. A well-formed PubMed search + appraisal is 20–60 min. At the point of care the realistic budget is <2 min, so evidence is often skipped. 

- Hallucination risk of general AI. LLMs fabricate plausible citations (wrong authors, wrong journals, non-existent — 

- DOIs) and misstate quantitative results. In medicine this is not a UX blemish it is a safety and liability failure. Need for evidence-backed answers. Clinicians reason from study design and effect size, not prose confidence. Any credible tool must preserve design labels, populations, comparators, and outcomes, and must let the user verify. 

Framing: This is fundamentally a retrieval and grounding problem, not a "better base model" problem. The scarce resource is trust, and trust is produced by provenance + faithfulness, not fluency. 

# 4. MVP Goals (Definition of Success) 

|Goal|Concrete success criterion (MVP)|
|---|---|
|Fast evidence retrieval|p50 end-to-end latency < 8s, p95 < 15s for a standard question|
|Evidence-grounded answers|≥95% of clinically material sentences carry≥1 valid citation|
|Transparent citations|100% of citations resolve to a real chunk + source doc; span highlight available|
|High trustworthiness|Faithfulness (claim supported by cited chunk)≥0.9 on eval set; zero fabricated references|
|Coverage honesty|System abstains ("insufcient evidence") rather than guessing when retrieval is weak|



Non-goals for MVP success: breadth beyond oncology, sub-second latency, mobile. 

# 5. MVP Scope 

### 5.1 Must Include 

- User authentication (email/password; clinician-gated waitlist). 

- Question input (natural language, optional structured filters). Medical evidence retrieval over the existing oncology corpus. 

- Hybrid search (dense + sparse/BM25). 

- Reranking (cross-encoder over fused candidates). 

- AI-generated answers constrained to retrieved evidence. 

- Inline citations (numbered, span-linked). 

- Source viewing (open the cited passage in context). 

- Evidence cards (per-source summary: study type, population, outcome, effect). Study-type identification (RCT / meta-analysis / systematic review / guideline). 

- Feedback collection (thumbs + reason + free text on answers and citations). 

### 5.2 Must Exclude (explicitly out of MVP) 

Mobile apps · EHR integration · Voice assistant · Multi-agent workflows · Knowledge graph · Personalized recommendations · Billing/subscriptions. 

Scope discipline. Each excluded item is deferred, not rejected (see §15). The MVP exists to validate one hypothesis: clinicians will trust and reuse an evidence-grounded, cited answer engine. 

# 6. User Journey (End-to-End) 

1. Ask. Rao types: "In HER2-low mBC, does T-DXd improve PFS vs chemo?" Optionally sets filters (study type = RCT, years = last 5). 

2. Interpret & retrieve. Query is normalized/expanded (acronyms → full terms), embedded, and run through hybrid retrieval (BM25 + dense) with metadata filters. 

3. Fuse & rerank. Candidates fused (RRF), then a cross-encoder reranks top-N for precision. 

4. Assemble context. Diversity- and section-balanced selection of chunks, with parent-context expansion and study-type weighting. 

5. Generate. LLM synthesizes an answer only from assembled context, inserting inline citation markers. 

6. Verify (automatic). Post-generation grounding check: every cited claim is validated against its source chunk; unsupported claims are dropped or flagged. 

7. Review. Rao reads the answer, sees evidence cards (RCT, N, comparator, HR/PFS), clicks a citation to view the exact source passage, and gives feedback. 

# 7. Functional Requirements 

### 7.1 Authentication 

- FR-A1: Email/password signup with verification; sessions via secure HTTP-only cookies / JWT. 

- FR-A2: Role field ( `clinician` , `researcher` , `admin` ); gated access (waitlist/allowlist for MVP). FR-A3: Rate limiting per user (Redis) to control LLM cost/abuse. 

### 7.2 Search / Question Input 

FR-S1: Free-text question (≤ 2,000 chars). 

- FR-S2: Optional structured filters: study type, publication year range, (future: population/intervention). FR-S3: Query normalization: acronym expansion, spelling, medical synonym handling. 

### 7.3 Retrieval 

- FR-R1: Hybrid retrieval (dense + BM25) with configurable weights. 

- FR-R2: Metadata pre-filtering (study type, date) applied at vector search time. 

- FR-R3: Cross-encoder reranking of fused top-K. 

- FR-R4: Deterministic, logged retrieval (store query, params, retrieved chunk IDs, scores) for eval and debugging. 

### 7.4 Evidence Display 

FR-E1: Evidence cards per source: title, authors (short), year, journal, study type badge, population, intervention/comparator, primary outcome + effect size when extractable, link to source. FR-E2: Sort/group by study-type strength and relevance. 

### 7.5 Citation Display 

FR-C1: Inline numbered citations `[1]` , `[2]` … mapped to evidence cards. 

FR-C2: Clicking a citation opens the exact cited span highlighted within the source passage. FR-C3: No citation may reference a source not in the assembled context (enforced server-side). 

### 7.6 Feedback 

FR-F1: Answer-level thumbs up/down + reason codes (inaccurate, unsupported, incomplete, great). FR-F2: Citation-level "does this source support the claim?" (yes/no). 

FR-F3: Feedback stored with full retrieval trace for offline evaluation. 

### 7.7 Administration 

FR-AD1: Admin dashboard: corpus stats, ingestion/embedding status, query volume, latency, feedback trends. FR-AD2: Re-index / re-embed controls; view a query's full trace (retrieved, reranked, context, answer). FR-AD3: Flagged-answer queue (down-voted or failed grounding) for review. 

# 8. Technical Architecture 

## 8.1 Component Overview 

```
                    ┌──────────────┐
   Browser (Next.js)│  Web Client  │
                    └──────┬───────┘
                           │ HTTPS/JSON
                    ┌──────▼───────┐
                    │  FastAPI     │  auth, orchestration, rate limit
                    │  (API layer) │
                    └──┬────┬───┬──┘
             ┌─────────┘    │   └──────────┐
        ┌────▼────┐   ┌─────▼────┐   ┌─────▼──────┐
        │Postgres │   │  Redis   │   │  Qdrant    │
        │(metadata│   │(cache,   │   │ (vectors + │
        │ +truth) │   │ ratelimit│   │  payload)  │
        └─────────┘   │ +queue)  │   └────────────┘
                      └──────────┘
   ┌──────────────────────────────────────────────┐
   │ Retrieval pipeline (in-process services)       │
   │ normalize → hybrid search → RRF → rerank →      │
   │ context assembly → LLM generate → grounding chk │
   └──────────────────────────────────────────────┘
                           │
                    ┌──────▼───────┐
                    │ LLM provider  │ (hosted API for MVP)
                    └──────────────┘
   [Offline] Ingestion (exists) → Parse → Chunk → Embed → Index
```

## 8.2 Assumption Review (confirm / challenge) 

|Assumption|Verdict|Reasoning|
|---|---|---|
|FastAPI backend|✅Confrm|Async I/O suits the fan-out (vector + BM25 + LLM) workload;frst-class Pydantic<br>validation; strong Python ML ecosystem for reranker/embeddings.|
|PostgreSQL|✅Confrm|Source-of-truth for users, papers, chunks metadata, feedback. Also gives us<br>**`pg_trgm`**/**`tsvector`**BM25-style searchand`pgvector`as a fallback—reduces<br>moving parts risk.|
|Redis|✅Confrm|Rate limiting, response/query caching, and a lightweight job queue for<br>embedding/reindex.|
|Qdrant|✅Confrm<br>(with note)|Excellent payloadfltering, hybrid support (named vectors + sparse), good<br>ANN/HNSW, easy self-host.Note:if we want to cut infra,`pgvector`covers MVP<br>scale, but Qdrant's native sparse+dense andflter performance justify it.|
|BGE-M3<br>embeddings|⚠Confrm as<br>primary,but<br>see §9.4|BGE-M3 is a strong multi-functional (dense+sparse+ColBERT) model and gives us<br>hybrid "for free." WerejectPubMedBERT as the primary retriever embedding (it's<br>an encoder for classifcation/NER, not tuned for retrieval) but consider domain-<br>adapted alternatives.|
|Cross-encoder<br>reranker|✅Confrm|Precision gains at top-k are large and worth the latency at our K. See §9.6 for<br>model choice.|
|LLM answer<br>generation<br>(hosted)|✅Confrm for<br>MVP|Buy-not-build for the base model; invest engineering in grounding, not in<br>serving weights. See §9.8.|



## 8.3 Data Flow (request path) 

1. Auth check + rate limit (Redis). 

2. Cache lookup (Redis) keyed by normalized query + filters → return if warm. 

3. Query normalization/expansion. 

4. Parallel retrieval: Qdrant dense (+ sparse) and BM25/keyword; metadata filters applied. 

5. Reciprocal Rank Fusion → cross-encoder rerank. 

6. Context assembly (diversity + section balance + parent expansion + study-type weighting). 

7. LLM generation with citation markers. 

8. Grounding verification (NLI/overlap) → finalize answer + evidence cards. 

9. Persist query, trace, answer, citations (Postgres); cache result (Redis). 

# 9. Retrieval Architecture (Deep Technical Section) 

## 9.1 Document Parsing & Extraction 

### 9.1.1 Core question: same pipeline for research papers vs guidelines? 

Decision: No — two parsing/extraction profiles, one shared chunking framework. Research papers (RCTs, meta-analyses, systematic reviews) and clinical guidelines (NCCN/ASCO/ESMO) have fundamentally different structure and information density, and conflating them degrades both retrieval and answer quality. 

|Dimension|Research papers|Clinical guidelines|
|---|---|---|
|Source<br>format|PMC JATSXML(preferred) or PDF|MostlyPDF(often long, tabular, algorithmic)|



|Structure|IMRaD (Intro/Methods/Results/Discussion), abstract,<br>tables, references|Recommendation statements,<br>evidence/consensus levels, treatment<br>algorithms, footnotes|
|---|---|---|
|Unit of<br>meaning|Afnding(efect size + CI in Results; claim in Discussion)|Arecommendation(statement +<br>strength/level of evidence + population)|
|Extraction<br>priority|Abstract, Methods (design/population/comparator),<br>Results (outcomes, HR/OR/RR, CI, p), tables|Recommendation text, category of<br>evidence/consensus, applicable<br>population/line of therapy|



Why not one pipeline: guideline "recommendations" are atomic, high-value units that must not be split mid-statement, and their strength label is the most important metadata. Papers need IMRaD-aware section tagging so we can section-balance context (§9.7) and weight Results/Methods appropriately. A single generic parser loses both. 

### 9.1.2 Structured vs unstructured handling 

- Prefer PMC JATS XML whenever available: sections, table markup, and references are machine-labeled → high-fidelity section detection and citation extraction essentially for free. 

- PDF path (fallback / guidelines): layout-aware parsing. 

   - Options: (a) `GROBID` (purpose-built for scholarly PDFs → TEI XML with sections, refs, affiliations); (b) generic layout models (e.g., `unstructured` , PyMuPDF + heuristics); (c) VLM/LLM-based parsing. 

   - Decision: GROBID for research-paper PDFs (best structure recovery for scholarly docs), layout parser + rule/LLM section classifier for guidelines (they aren't IMRaD, so GROBID's model doesn't fit). Use PyMuPDF for text/coordinate extraction to enable span offsets (needed for span-level citation, §9.8). 

   - 

   - Rejected: pure LLM parsing of every PDF as the default too costly/slow at ingest scale and nondeterministic; used only as a repair path for documents that fail structured parsing. 

### 9.1.3 Tables, figures, supplementary data 

- Tables are where the numbers live (outcome tables, baseline characteristics). Extract table structure (from JATS XML directly; from PDF via GROBID/ `img2table` /camelot-style extraction). Store each table as: (a) a linearized text chunk (row/col headers preserved) for retrieval, and (b) structured JSON in metadata for the evidence card (e.g., primary outcome, HR, CI). 

- Figures: OCR captions and extract caption text as chunks; MVP does not parse figure images (Kaplan–Meier curve reading is roadmap). Caption + surrounding Results text usually restates the key numbers. 

- Supplementary data: ingested but down-weighted in ranking (low signal-to-noise); indexed for recall-mode (researchers) but not surfaced by default. 

## 9.2 Chunking Strategy 

### 9.2.1 Options compared 

|Strategy|Pros|Cons|Fit for medical lit|
|---|---|---|---|
|Fixed-size (token window +<br>overlap)|Simple, uniform,<br>predictable embedding<br>cost|Splits mid-fnding; breaks tables;<br>ignores structure|Poor alone—<br>destroys the "one<br>fnding per chunk"<br>property|
|Semantic chunking<br>(embedding-similarity<br>boundaries)|Coherent topical units|Compute at ingest; boundaries<br>can still cut tables/stats;<br>nondeterministic|Useful for long<br>Discussion prose;<br>weak on structured<br>content|



|Section-based(IMRaD /<br>recommendation-based)|Aligns with how clinicians<br>read; enables section<br>balancing & weighting|Sections vary wildly in length<br>(Results can be huge)|Strong—but needs<br>sub-splitting for long<br>sections|
|---|---|---|---|
|Parent-child(small child<br>chunks for retrieval, larger<br>parent for context)|Precise retrieval + rich<br>generation context;<br>reduces fragmentation|More storage; must track<br>relationships|Excellent—<br>precision without<br>losing context|
|Hierarchical(doc→section<br>→paragraph→sentence)|Multi-granularity<br>retrieval, great<br>provenance|Most complex; more<br>indexing/orchestration|Powerful but heavier<br>than MVP needs|



### 9.2.2 Decision 

Section-aware parent-child chunking, with content-type-specific rules. Concretely: 

1. Segment by structure first (IMRaD for papers; recommendation units for guidelines) — this is the parent layer. 

2. Within each section, create child chunks targeting ~256–320 tokens with ~15% overlap, but never split a table row, a statistical result sentence, or a guideline recommendation statement across chunks (structure-guarded splitting). 

3. Store parent context (the full section or the recommendation + its evidence level) and link child → parent. Retrieve on children (precision); expand to parent at context-assembly time (recall/coherence). 

4. Guidelines: each recommendation statement is its own atomic parent unit (child = the statement; parent = statement + evidence level + population + surrounding algorithm note). Recommendations are never merged. 

Why this combination: medical relevance is localized ("finding-level"), which argues for small child chunks and precise retrieval; but faithful generation needs the surrounding methods/population context, which argues for parent expansion. Section-awareness lets us do study-type/section weighting (§9.7). This gives parent-child's precision+context benefits without the full operational weight of a 4-level hierarchy. 

Why reject the others as primary: fixed-size alone corrupts numeric findings; pure semantic chunking is nondeterministic and unnecessary once we have structure; full hierarchical is over-engineered for a single-domain MVP (revisit in scale-up, §15). 

## 9.3 Metadata Design 

Metadata is a first-class retrieval lever, not decoration. Stored in Postgres (truth) and mirrored into Qdrant payload (for filtering). 

Document-level: <mark>`doc_id, source (pubmed/pmc/guideline_org), pmid, pmcid, doi, title, journal, publication_year, authors, study_type {RCT, meta_analysis, systematic_review, guideline, other}, evidence_level (guidelines), mesh_terms[], oncology_subdomain, retraction_flag, license/open_access, ingest_date`</mark> 

Clinically structured (extracted; best-effort, confidence-scored): <mark>`population (condition, stage, biomarker e.g. HER2-low), intervention[], comparator[], primary_outcome, effect_measure {HR/OR/RR/median_PFS/OS},`</mark> `effect_value, ci_low, ci_high, p_value, sample_size_n` 

Chunk-level: <mark>`chunk_id, doc_id, parent_id, section {abstract|intro|methods|results|discussion|table|recommendation}, char_start, char_end (for span citations), token_count, content_type {prose|table|stat|recommendation}, sparse_vector_ref`</mark> 

How metadata improves retrieval: 

- Pre-filtering (study type, year, biomarker) shrinks the candidate space to clinically valid docs before ANN — improving both precision and latency. 

- Study-type weighting in fusion/rerank encodes evidence hierarchy (RCT > obs). Evidence cards are populated directly from structured metadata. 

Retraction flag hard-excludes retracted papers (safety). 

_`ASSUMPTION`_ : structured clinical fields are extracted with an LLM/rules pass at ingest and stored with a confidence score; lowconfidence fields are shown as "not extracted" rather than guessed. 

## 9.4 Embedding Strategy 

### 9.4.1 Options 

|Model|Type|Strengths|Weaknesses|
|---|---|---|---|
|BGE-M3|Multilingual,multi-<br>vector (dense + sparse<br>+ ColBERT), retrieval-<br>tuned|Hybrid in one model; strong<br>general + long-context (8k);<br>mature|Not medical-domain-pretrained|
|PubMedBERT|Domain MLM encoder|Biomedical<br>vocabulary/knowledge|Not a retrieval model —needs<br>fne-tuning to produce useful<br>sentence embeddings; weak of-<br>the-shelf for similarity search|
|MedCPT|Biomedicalretrievalbi-<br>encoder (trained on<br>PubMed click logs)|Purpose-built biomedical<br>retrieval; strong on PubMed-<br>style queries|English-only; shorter context; less<br>fexible than M3's hybrid|
|General SOTA<br>(e.g., E5-large /<br>GTE-large)|Retrieval bi-encoder|Strong general retrieval|No domain signal; no built-in<br>sparse|



### 9.4.2 Decision 

Primary: BGE-M3 as the retrieval embedding, using its dense + sparse outputs to power hybrid search natively. Secondary/experiment: MedCPT as a domain-specialized dense retriever to A/B against BGE-M3 on our eval set. 

##### Reasoning: 

- BGE-M3 gives us dense and learned-sparse vectors from a single model, simplifying the hybrid stack (one model, two Qdrant vectors) and its 8k context tolerates our larger parent chunks. 

- We reject PubMedBERT as the retriever — it's designed for token-level tasks (NER/classification), and using it for dense retrieval without heavy fine-tuning underperforms modern retrieval models. It may be retained for auxiliary tasks (study-type classification, entity tagging at ingest). 

- MedCPT is the most credible domain-specialized challenger; because embedding choice is empirical, we keep it as a first-class experiment rather than a memory-based decision. The cross-encoder reranker (§9.6) also absorbs much of the domain-adaptation burden, which lowers the risk of using a strong general retriever. 

### 9.4.3 Domain adaptation considerations 

- Medical queries are acronym- and synonym-heavy → invest in query normalization/expansion (§9.5) rather than premature embedding fine-tuning. 

- Keep the door open to fine-tuning BGE-M3 on (clinical question **→** relevant chunk) pairs mined from feedback logs once we have volume (roadmap). 

- Reranker does the heavy semantic lifting at top-k, reducing sensitivity to the base embedding's domain gap. 

## 9.5 Vector Search & Hybrid Retrieval 

- Dense captures paraphrase/semantic match ("does drug X help" ≈ "efficacy of X"). Sparse/BM25 captures exact tokens that dense models blur — drug names, gene/biomarker codes (HER2, EGFR), trial acronyms (DESTINY-Breast04), dosages. In medicine, exact-term matching is critical, so sparse is not optional. 

- 

- Implementation: Qdrant named vectors dense (BGE-M3 dense) + sparse (BGE-M3 learned sparse or BM25). Run both; fuse. 

- Fusion: Reciprocal Rank Fusion (RRF) as default — robust, score-scale-agnostic, no tuning of incompatible score ranges. Provide a tunable weighted fusion (α·dense + (1−α)·sparse) as an experiment knob; RRF wins for MVP because it's stable without per-query calibration. 

- Metadata filtering applied inside Qdrant (payload filters) so we only ANN-search clinically valid candidates. 

- Query expansion/rewriting: lightweight LLM/rule step that (a) expands acronyms (T-DXd → trastuzumab deruxtecan), (b) adds MeSH synonyms, (c) optionally decomposes multi-part questions. Decision: enable rule-based 

- acronym/synonym expansion by default; enable LLM query rewriting behind a flag (adds latency + variability; measure lift before defaulting on). 

Retrieval budget (MVP): dense top-100 + sparse top-100 → RRF → top-50 → rerank → top-8–12 to context. 

## 9.6 Reranking 

|Option|Accuracy|Latency|Notes|
|---|---|---|---|
|Bi-encoder only (no rerank)|Baseline|Fastest|Missesfne-grained query–passage<br>interaction|
|Cross-encoder(query+passage<br>jointly encoded)|Highest<br>precision@k|Higher (scores each<br>pair)|Standard for high-stakes RAG;<br>latency bounded by K|
|Late-interaction (ColBERT-style)|Between the two|Medium|BGE-M3 exposes this; good middle<br>ground|
|LLM-as-reranker|High,fexible|Highest<br>cost/latency|Overkill for MVP|



Decision: cross-encoder reranker over the fused top-50, keeping top-8–12. For a medical product, top-k precision dominates perceived trust; reranking 50 pairs is a bounded, cache-able cost (~tens of ms on GPU / low hundreds on CPU). Candidate models to benchmark: `BAAI/bge-reranker-v2-m3` (pairs naturally with BGE-M3), and a MedCPT cross-encoder for domain specialization. Latency mitigation: cap rerank input at 50, truncate passages, batch, and cache by (query_hash, chunk_id). 

Rejected: no-rerank (precision too low for trust), LLM reranker (cost/latency not justified at MVP). 

## 9.7 Context Assembly 

Goal: hand the LLM the smallest set of chunks that fully supports a faithful answer. 

- How many chunks: 8–12 child chunks, expanded to their parent context, capped to a token budget (e.g., ~6–8k tokens of evidence). More chunks = better; they dilute attention and raise cost/latency. 

- Diversity vs relevance: apply MMR-style de-duplication so we don't fill the window with 6 near-identical passages from the same paper. Enforce a per-document cap (e.g., ≤3 chunks/doc) so multiple trials are represented. 

- Section balancing: prefer a mix of Methods (design/population/comparator) and Results (outcomes/effect sizes); Discussion/conclusions are included but capped (they overstate). For guidelines, the recommendation + evidence level is always included whole. 

- Study-type weighting: boost RCTs and meta-analyses of RCTs; include guidelines as authoritative recommendations; observational/supplementary only to fill gaps. This weighting is applied as a re-ranking bias, and the chosen study types are surfaced in evidence cards. 

- Provenance packaging: each context chunk carries `[doc_id, chunk_id, char_start/end, study_type]` so citations can be span-linked and validated post-hoc. 

## 9.8 Answer Generation 

### 9.8.1 LLM selection 

|Option|Pros|Cons|
|---|---|---|
|Hosted frontier model via<br>gateway(e.g., top-tier general<br>model)|Best instruction-following & synthesis;<br>strong faithfulness with good prompting; no<br>serving ops|Per-token cost; data-handling due<br>diligence|
|Open-weight general model<br>(self-host)|Data control, cost at scale|Serving ops + GPU cost; weaker<br>synthesis at MVP sizes|
|||Smaller ecosystems;grounding,|
|Biomedicalfne-tuned LLM|Domainfuency|not domain trivia, is our<br>bottleneck|



Decision: use a hosted frontier general-purpose LLM via a provider gateway for MVP, selected empirically for faithfulness + instruction-following on our eval set, accessed through an abstraction layer so we can swap models. Rationale: our failure mode is ungrounded generation, which is solved by retrieval + prompting + verification, not by a medically fine-tuned model. Buy the model; build the grounding. 

_`ASSUMPTION`_ : for MVP we accept hosted inference under a BAA/data-processing-appropriate provider; no PHI is sent (queries are clinical questions, not patient identifiers). This is revisited for production/EHR phase. 

### 9.8.2 Prompt design (grounding contract) 

- System role: "You are a clinical evidence assistant. Answer only using the numbered sources provided. If the evidence is insufficient or conflicting, say so explicitly. Do not use outside knowledge. Every clinical claim must cite the source number(s) that support it." 

- Structure: (1) direct answer with inline `[n]` citations; (2) brief evidence summary noting study types and key effect sizes; (3) explicit limitations / conflicting-evidence note; (4) abstain path. 

- Sources injected as `[[n]] (study_type, year) <chunk text>` with stable numbering that maps to evidence cards. 

Low temperature; deterministic settings for reproducibility. 

### 9.8.3 Grounding strategy & citation insertion 

- Citation granularity: chunk-level attribution + span-level highlight. The model cites chunk `[n]` ; at render time we locate the supporting span within that chunk (via sentence-embedding/NLI match between the generated claim and chunk sentences) and highlight it using stored `char_start/end` . This gives span-level transparency without asking the LLM to emit fragile character offsets. 

- Why not pure span-level generation: models emit unreliable offsets and hallucinate spans; post-hoc span alignment is more robust. Why not pure chunk-level display: clinicians want to see the exact sentence, not scan a paragraph. 

## 9.9 Hallucination Mitigation (defense in depth) 

1. Retrieval grounding + closed-book prohibition (prompt contract; outside knowledge forbidden). 

2. Citation-set enforcement: server rejects/strips any citation index not present in the assembled context (a model can't invent a source that wasn't provided). 

3. Post-generation faithfulness verification: for each cited sentence, run an NLI/entailment or embedding-overlap check between the claim and its cited chunk. Unsupported claims are (a) dropped, (b) marked "unsupported," or (c) trigger regeneration, per severity. 

4. Numeric guardrail: any effect size / dosage / statistic in the answer must appear in a cited chunk (regex + value match); otherwise flag. 

5. Abstention path: if fused/reranked scores are below threshold or verification fails broadly, return "insufficient highquality evidence" instead of a confident answer. 

6. Confidence scoring (surfaced to user): a composite of retrieval strength (top rerank scores), evidence quality (study types present), and grounding-check pass rate → shown as a calibrated High/Moderate/Low badge. 

7. Retraction exclusion at retrieval time (safety hard-stop). 

# 10. Database Design 

## 10.1 Entities & key fields (PostgreSQL = source of truth) 

- users `(id, email, password_hash, role, created_at, verified, status)` 

- papers/documents <mark>`(doc_id, source, pmid, pmcid, doi, title, journal, year, authors_json, study_type, evidence_level, oncology_subdomain, mesh_terms, retraction_flag,`</mark> 

- <mark>`structured_clinical_json, license, ingest_date)`</mark> 

- chunks <mark>`(chunk_id, doc_id FK, parent_id, section, content_type, text, char_start, char_end, token_count, embedding_status, qdrant_point_id)`</mark> 

- queries `(query_id, user_id FK, raw_text, normalized_text, filters_json, created_at, latency_ms)` retrieval_traces `(trace_id, query_id FK, retrieved_json[chunk_id,scores], reranked_json,` <mark>`context_chunk_ids, params_json)`</mark> 

- answers `(answer_id, query_id FK, text, confidence, model, prompt_version, created_at)` 

- citations <mark>`(citation_id, answer_id FK, chunk_id FK, doc_id FK, marker_index, span_start, span_end, verification_status)`</mark> 

- feedback <mark>`(feedback_id, user_id FK, answer_id FK, citation_id FK nullable, vote, reason_code, free_text, created_at)`</mark> 

## 10.2 Relationships 

`users 1—* queries 1—1 answers 1—* citations *—1 chunks *—1 documents` ; `queries 1—1 retrieval_traces` ; `feedback` references `answers` / `citations` . Vectors live in Qdrant, keyed by `chunk_id` (payload mirrors filterable metadata); Postgres holds canonical text + offsets. 

## 10.3 Indexing strategy 

- B-tree on FKs and `queries.created_at` , `documents.study_type` , `documents.year` . 

- GIN on `mesh_terms` , `authors_json` , and a `tsvector` column for keyword/BM25-style search (backup to Qdrant sparse). 

- `pg_trgm` on `title` for fuzzy lookup. 

- Partial index on `documents.retraction_flag = true` and on `answers` with failed verification (admin queue). 

- Qdrant: HNSW (tuned `m` / `ef` ) for dense; sparse index for learned-sparse; payload indexes on `study_type` , `year` , biomarker. 

# 11. API Requirements 

Base: `/api/v1` . Auth via bearer/session cookie. JSON. 

### **`POST /auth/signup`** / **`POST /auth/login`** 

Req: `{ "email": "...", "password": "..." }` → Res: `{ "token": "...", "user": { "id", "role" } }` 

### **`POST /search`** (main endpoint) 

Request: 

```
{
```

- `"question": "In HER2-low mBC, does T-DXd improve PFS vs chemo?",` 

```
"filters": { "study_type": ["RCT","meta_analysis"], "year_from": 2020 },
```

```
"mode": "fast"
}
```

Response (abridged): 

```
{
"query_id": "q_123",
"answer": "Trastuzumab deruxtecan significantly improved PFS versus physician's-choice
chemotherapy in HER2-low mBC [1][2]...",
"confidence": "high",
"citations": [
    { "marker": 1, "doc_id": "d_88", "chunk_id": "c_5521",
"title": "Trastuzumab Deruxtecan in HER2-Low ... (DESTINY-Breast04)",
"study_type": "RCT", "year": 2022,
"span": { "start": 1420, "end": 1560 } }
  ],
"evidence_cards": [
    { "doc_id": "d_88", "study_type": "RCT", "n": 557,
"population": "HER2-low mBC", "intervention": "T-DXd",
"comparator": "physician's choice chemo",
"primary_outcome": "PFS", "effect": { "measure": "HR", "value": 0.50, "ci": [0.40, 0.63] },
"journal": "NEJM", "year": 2022 }
  ],
"latency_ms": 6120
}
```

### — **`GET /documents/{doc_id}`** full metadata + sections for source viewing. 

### **`GET /documents/{doc_id}/chunk/{chunk_id}`** — chunk text + parent context + span offsets (for highlighting). 

#### **`POST /feedback`** 

Req: <mark>`{ "answer_id": "...", "citation_id": null, "vote": "down", "reason_code": "unsupported", "text": "..." }`</mark> → `{ "ok": true }` 

**`GET /admin/stats`** · **`GET /admin/query/{query_id}/trace`** · **`POST /admin/reindex`** (admingated). 

# 12. UI/UX Requirements 

- Landing page: value prop ("Evidence-grounded clinical answers, with citations you can verify"), example question, trust framing (study-type labels, span citations), waitlist/login CTA. Clean, clinical, high-contrast; 1 brand color + neutrals. 

- Login/Signup: minimal; role selection; verification notice; clinician-gating copy. 

- Search page: prominent single question box; collapsible filters (study type chips, year slider); example prompts; recent queries. Fast/Recall mode toggle (recall = roadmap-lite). 

- Answer page (core): 

   - Left/main: the answer with inline numbered citations; confidence badge; limitations/conflicting-evidence callout. 

   - Right/aside: evidence cards grouped by study-type strength (RCT badge, N, comparator, effect size), each linking to source. 

Clicking `[n]` opens the source panel with the exact span highlighted. 

Feedback controls at answer- and citation-level. 

- Source panel: document header (title, journal, year, study-type badge, DOI/PMID link), section navigation, highlighted cited span in context. 

- Admin dashboard: corpus/ingestion/embedding status, latency + volume charts, feedback trends, flagged-answer queue, per-query trace viewer, reindex controls. 

Wireframe intent: two-pane "answer + evidence" is the signature screen; everything reinforces verify in one click. 

# 13. Trust & Safety 

- Citation requirements: every clinically material sentence must carry ≥1 valid, resolvable citation; citations restricted to the provided context set (server-enforced); span highlight available. 

- Hallucination mitigation: the §9.9 defense-in-depth stack (closed-book prompt, citation enforcement, NLI/numeric verification, abstention, confidence scoring, retraction exclusion). 

- Evidence transparency: study-type labels, effect sizes with CIs, and full source access on every answer; show what evidence was used. 

- Limitations disclosure: persistent disclaimer — decision support, not medical advice; verify against primary sources and clinical judgment; corpus is oncology-focused and time-bounded. Explicitly surface conflicting evidence and evidence gaps rather than smoothing them over. 

- Safety hard-stops: retracted papers excluded; out-of-scope questions (non-oncology, at MVP) detected and flagged as low-coverage. 

# 14. Metrics & Evaluation 

### 14.1 Retrieval metrics (offline, on a labeled question **→** relevant-chunk set) 

- Recall@k (k = 10/20/50) — do we retrieve the right evidence at all? 

- MRR / nDCG@10 — is the best evidence near the top after rerank? 

- Precision@k post-rerank — top-k cleanliness. 

- Ablations: dense-only vs sparse-only vs hybrid; BGE-M3 vs MedCPT; rerank on/off. 

### 14.2 Answer quality 

- 

- Faithfulness / groundedness % of claims entailed by cited chunks (automated NLI + human spot-check). — 

- Citation validity % citations that actually support the claim (human + FR-F2 feedback). 

- 

- Answer relevance / completeness expert-rated (clinician panel) on a rubric. 

- Hallucination rate — % answers with ≥1 unsupported/ fabricated claim (target ~0 for references). 

- Abstention correctness — did we abstain when evidence was genuinely insufficient? 

### 14.3 User satisfaction 

Thumbs-up rate; citation-support agreement rate; reuse/return rate; qualitative interviews with design partners. 

### 14.4 Performance 

p50/p95 end-to-end latency; retrieval vs rerank vs LLM time breakdown; cache hit rate; cost per query. 

Eval harness is a build item, not an afterthought (see §16): a versioned gold-set + automated scoring is required to make the §9 decisions empirically rather than by opinion. 

# 15. Future Roadmap 

Phase 1 — MVP (this PRD): oncology corpus, auth, hybrid retrieval + rerank, grounded answers, span citations, evidence cards, study-type labels, feedback, admin, eval harness. 

Phase 2 — Beta: broaden corpus beyond oncology; LLM query rewriting on by default (if it wins eval); recall/"systematic scan" mode for researchers; figure/Kaplan–Meier extraction; fine-tune BGE-M3 on feedback-mined pairs; hierarchical chunking where it pays off; export/reference-manager integration; richer confidence calibration; team accounts. 

Phase 3 — Production: EHR/context integration, personalized recommendations, knowledge-graph enrichment (drug–disease– trial relations), multi-agent workflows for complex multi-part questions, mobile, billing/subscriptions, voice — each previously excluded from MVP is a Phase 3 candidate, gated by demonstrated trust metrics and compliance (HIPAA/BAA, SOC 2). 

# 16. Engineering Milestones 

Current state: ✅ Ingestion pipeline (discover/download/parse/store PubMed + PMC; oncology corpus; RCT/metaanalysis/systematic-review/guideline types). 

Recommended sequence (each milestone ends with a measurable gate): 

1. Parsing & extraction hardening — dual profiles (paper vs guideline), JATS-first + GROBID PDF path, table/section extraction, structured clinical-field extraction, span offsets. Gate: ≥95% docs section-tagged; tables captured. 

2. Chunking — section-aware parent-child with structure-guarded splitting; store parent/child + offsets. Gate: deterministic re-chunk; no split findings/recommendations. 

3. Embeddings — BGE-M3 dense+sparse; MedCPT experiment path; batch embed pipeline (Redis queue). Gate: full corpus embedded; A/B harness ready. 

4. Vector DB & hybrid retrieval — Qdrant collections (dense+sparse named vectors, payload indexes) + 

- BM25/ `tsvector` fallback; RRF fusion; metadata filters. Gate: Recall@20 on gold set meets threshold. 

5. Reranking — cross-encoder ( `bge-reranker-v2-m3` vs MedCPT CE); caching. Gate: nDCG@10 uplift vs no-rerank demonstrated. 

6. Context assembly + answer generation — MMR diversity, per-doc cap, section balance, study-type weighting; grounding-contract prompt; LLM via gateway abstraction. Gate: faithfulness ≥0.9 on eval set. 

7. Grounding/verification layer — citation-set enforcement, NLI/numeric checks, span alignment, abstention, confidence scoring. Gate: ~0 fabricated references; verification wired into responses. 

8. Backend APIs — FastAPI endpoints (§11), auth, rate limiting, caching, trace logging. Gate: full request path + traces persisted. 

9. Frontend — search, two-pane answer+evidence, source panel with span highlight, feedback, admin dashboard. Gate: end-to-end demo with design partners. 

10. Eval harness & testing — gold set, automated retrieval + faithfulness scoring, load/latency tests. Gate: dashboards green; p95 <15s. 

11. Launch (private beta) — clinician allowlist, monitoring, feedback loop live. 

## — Appendix A Consolidated Decision Log 

|Area|Decision|Rejected alternatives (why)|
|---|---|---|
|Papers vs<br>guidelines|Separate parse/extract profles, shared<br>chunking framework|Single pipeline (loses recommendation atomicity & IMRaD<br>weighting)|
|PDF parsing|JATS XMLfrst; GROBID for paper PDFs;<br>layout+classifer for guidelines|Pure-LLM parse (cost/nondeterminism); generic-only (poor<br>structure)|
|Chunking|Section-awareparent-child, structure-<br>guarded splits|Fixed-size (breaksfndings); pure semantic<br>(nondeterministic); full hierarchical (over-engineered for<br>MVP)|
|Embeddings|BGE-M3(dense+sparse), MedCPT as<br>A/B|PubMedBERT as retriever (not a retrieval model); general-<br>only (no sparse)|



|Hybrid fusion|RRFdefault, weighted as knob|Weighted-only (needs per-query calibration)|
|---|---|---|
|Reranker|Cross-encodertop-50→top-8–12|No-rerank (low precision); LLM-rerank (cost/latency)|
|Citations|Chunk-level attribution +post-hoc<br>span highlight|Pure span-gen (unreliable ofsets); pure chunk-display<br>(poor transparency)|
|LLM|Hosted frontier model via gateway<br>abstraction|Self-host open-weight (ops); biomedical-FT LLM<br>(grounding, not trivia, is the bottleneck)|
|Vector DB|Qdrant(native hybrid + payload<br>flters); pgvector fallback|pgvector-only (weaker hybrid/flter perf at scale)|



## — Appendix B Explicit Assumptions 

1. Structured clinical fields are extracted at ingest with confidence scores; low-confidence fields shown as "not extracted," never guessed. 

2. MVP queries are clinical questions with no PHI; hosted LLM inference is acceptable under appropriate data terms for MVP. 

3. Corpus is oncology-focused and time-bounded; out-of-scope questions are flagged as low-coverage rather than answered speculatively. 

4. Latency targets (p50 <8s, p95 <15s) assume GPU-backed reranker/embedding inference or a low-latency hosted equivalent. 


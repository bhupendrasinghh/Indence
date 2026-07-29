# Technical Design Document — Evidence-Grounded Clinical Q&A MVP

**Status:** implementation-ready design  
**Product scope:** oncology-focused, evidence-grounded clinical Q&A private beta  
**Design authority:** [PRD.pdf](/Users/bhupendrasingh/Desktop/PRD.pdf) and [MVP_ARCHITECTURE_BLUEPRINT.md](/Users/bhupendrasingh/Desktop/MVP_ARCHITECTURE_BLUEPRINT.md)  
**Audience:** the three-engineer MVP team and coding agents working module-by-module

## 1. Executive Summary

**Traceability:** PRD §§1, 4–7, 13–16; Architecture §§1–2, 4–8, 17–18.

Build one modular-monolith product that lets gated clinicians and researchers ask an oncology question and receive either a verified, evidence-grounded answer or a safe abstention. The browser talks only to a Next.js application. The Next.js application proxies same-origin API calls to a FastAPI application. The FastAPI codebase runs in two roles: an online API process and an offline worker process. PostgreSQL is the canonical store for users, immutable evidence, answer provenance, traces, jobs, feedback, and audit events. Qdrant is a rebuildable dense-and-sparse retrieval projection; Redis is only a short-lived cache, rate-limit, idempotency, and locking layer.

The central publish invariant is:

> An answer can be marked `published` only when every material displayed claim has at least one verified citation to a stored, displayable source span in the active corpus snapshot.

The LLM is a bounded synthesizer, not an evidence source. It receives a closed `EvidenceBundle`, returns only structured claim objects containing supplied evidence and sentence IDs, and is never streamed directly to the user. The server resolves source spans, validates cited IDs and numeric values, runs semantic-support screening, and then either publishes the answer or returns an abstention. This directly implements the PRD's grounded-or-silent value proposition and its latency, citation, and faithfulness gates.

**Trade-off:** a synchronous, verification-first path costs more latency than streaming a model response, but it prevents unverified clinical text from appearing. The explicit p95 target of under 15 seconds makes this trade-off acceptable for MVP.

### Scope boundary

Included: gated email/password access; question input and filters; hybrid retrieval; cross-encoder reranking; source-backed answer generation; span-linked citations; evidence cards; study-type labels; feedback; admin corpus and trace controls; evaluation harness.

Excluded: mobile, EHR/PHI ingestion, personalized advice, voice, billing, team tenancy, social login/SSO, API tokens, knowledge graph, multi-agent workflows, self-hosted model serving, Kubernetes, Kafka, a separate search cluster, and recall/systematic-scan mode. The absence of these features is intentional and must be preserved in implementation.

## 2. Traceability Conventions, Reconciliation, and Assumptions

**Traceability:** PRD §§5, 7, 11–16 and Appendix B; Architecture §§1, 10–11, 15–17, 20.

### 2.1 Reference convention

- `FR-*` means the functional requirement identifiers in PRD §7.
- `PRD §x` means a non-numbered product, safety, metric, UX, or milestone requirement.
- `ARC §x` means the architecture blueprint section that constrains implementation.
- `AC-*` is an acceptance criterion defined by this TDD. It does not create a product feature; it makes an existing PRD/architecture requirement testable.

Every pull request that changes retrieval, verification, persistence, security, or public API behavior must name the affected PRD and ARC references in its description. A change affecting answer behavior must also identify the retrieval profile, prompt, verifier, or corpus version it changes.

### 2.2 Source reconciliation

| Topic | Source difference | TDD decision | Rationale |
|---|---|---|---|
| Main answer endpoint | PRD §11 calls it `POST /search`; ARC §10 defines `POST /api/v1/answers`. | Implement `POST /api/v1/answers` only. | The architecture's OpenAPI contract is the implementation authority. Semantics remain the same. |
| Request mode | PRD §11 uses `fast`; PRD §12 mentions a recall toggle; ARC §10 limits MVP to `standard`. | Accept `mode: "standard"` only; reject any other value with `422`. | Recall mode is explicitly future/roadmap scope; accepting it would imply an unbuilt product behavior. |
| Session choice | PRD FR-A1 permits secure cookies or JWT; ARC §11 chooses opaque server-side cookies. | Use opaque, revocable sessions in a `__Host-session` cookie. | Server-side revocation and role-gate changes are simpler and safer for MVP. |
| Hybrid sparse leg | PRD allows dense + sparse/BM25; ARC §7 selects BGE-M3 learned sparse in Qdrant. | Use BGE-M3 dense and learned-sparse vectors in Qdrant; keep PostgreSQL `tsvector` only for diagnostics/fallback evaluation. | Matches the architecture's single-model hybrid profile without adding a search cluster. |

### 2.3 Explicit assumptions and open operational decisions

| ID | Assumption or decision needed | Implementation treatment | Owner/gate |
|---|---|---|---|
| A-01 | The existing ingestion pipeline can emit the versioned manifest defined in ARC §8.1, including canonical text and stable locators. | Build the importer against the JSON schema. Records without text or locators enter `citation_not_ready` and cannot publish. | Corpus owner; required before Increment 1 gate. |
| A-02 | Structured clinical fields are best-effort extraction results, not ground truth. | Store `{value, confidence, source_locator}`; render low-confidence values as `Not extracted`. | Corpus/evidence owner; fixture tests required. |
| A-03 | MVP questions contain no PHI and a provider with appropriate data terms will be selected. | Run PHI/scope guard before cache, model, or provider calls. Do not enable production traffic until legal/data-processing review is complete. | Product/security owner; beta launch gate. |
| A-04 | The exact hosted LLM, embedding endpoint, reranker endpoint, and error tracker are not selected. | Select by versioned evaluation; expose only provider ports and environment configuration. No automatic multi-provider fallback. | Technical lead; required before production deployment. |
| A-05 | Retention periods, password policy values, verification-token lifetime, session lifetime, and numeric rate limits are not supplied. | Keep them typed, centrally configured, and documented in `.env.example`; do not hard-code policy values. Security owner must approve production values before beta. | Security owner; beta launch gate. |
| A-06 | Guideline display rights may vary by document revision. | Display full text only when `display_rights` permits; otherwise show allowed metadata and canonical external link. | Corpus/legal owner; publish gate. |
| A-07 | The PRD's metric “Recall@20 meets threshold” lacks a numeric threshold. | Store the agreed threshold in the versioned evaluation profile before promotion. The global release thresholds remain p50 < 8 s, p95 < 15 s, citation coverage >= 95%, faithfulness >= 0.90, zero fabricated references, and 100% resolvable citations. | Clinical/product owner; evaluation gate. |

**Trade-off:** carrying operational decisions as explicit gated assumptions leaves a small amount of deployment configuration unresolved, but avoids silently inventing product or compliance requirements while still giving implementers a deterministic configuration seam.

## 3. Requirement-to-Component Mapping

**Traceability:** PRD §4, §5.1, §7, §13–14; ARC §§4–8, 10–13, 18.

| PRD requirement | Implementation components | Primary persisted evidence / contract | Verification evidence |
|---|---|---|---|
| FR-A1 — email/password, verification, secure session | Next.js auth screens; `identity`; PostgreSQL `users`, `sessions`; email adapter | opaque cookie, hashed password and verification token | signup/login/logout/verification integration tests |
| FR-A2 — roles and gated access | `identity`, API dependencies, admin UI | `users.role`, `users.status`, `audit_events` | role/status matrix tests |
| FR-A3 — per-user rate limiting | `identity`, `answering`, Redis adapter | atomic Redis keys, request ID logs | concurrent quota tests and 429 contract tests |
| FR-S1 — 2,000-character free text | web question form; answers DTO; query normalizer | `query_runs.raw_text` and `normalized_text` | boundary and Unicode tests |
| FR-S2 — study type/year filters | web filters; `retrieval`; Qdrant payload filters | `query_runs.filters`, trace configuration | Qdrant filter and trace tests |
| FR-S3 — acronym, spelling, synonym normalization | `retrieval.normalization` | normalization result in `retrieval_traces` | deterministic normalization fixtures |
| FR-R1 — hybrid dense+sparse/BM25 with configurable fusion | worker, Qdrant adapter, `retrieval` | versioned retrieval profile, dense/sparse candidate stages and scores | dense/sparse/hybrid and fusion-profile ablation report |
| FR-R2 — metadata pre-filtering | `retrieval`, corpus projection | Qdrant filter payload and hydration state | test that excluded documents never reach context |
| FR-R3 — cross-encoder reranking | reranker adapter, `retrieval` | reranked stage candidates and model version | top-50 cap and nDCG uplift tests |
| FR-R4 — deterministic logged retrieval | `answering`, `retrieval`, `admin` | immutable run, trace, candidates, config and timings | trace replay with fakes |
| FR-E1 — evidence cards | `evidence`, corpus, web evidence panel | `evidence_card_data`, source metadata, locator | low-confidence and rights-policy rendering tests |
| FR-E2 — study-type/relevance ordering | `evidence`, `retrieval`, web | selected evidence order and study labels | order is explainable in trace and UI test |
| FR-C1 — inline numbered citations | `verification`, `evidence`, answer UI | `answers`, `answer_claims`, `citations.marker_index` | marker-to-card mapping contract test |
| FR-C2 — exact highlighted span | `evidence`, corpus source context API | immutable chunk-relative span and source locator | source highlight E2E test |
| FR-C3 — citations limited to context | `generation`, `verification` | `EvidenceBundle` IDs and verification result | unknown-ID/mismatched-snapshot rejection tests |
| FR-F1 — answer feedback | `feedback`, web | `feedback` with vote/reason/free text | ownership and reason-code tests |
| FR-F2 — citation-support feedback | `feedback`, evidence UI | `feedback.citation_id` linked to answer | citation belongs-to-answer constraint test |
| FR-F3 — feedback with trace | `feedback`, `admin`, PostgreSQL | answer/query/trace foreign keys | admin query trace shows feedback linkage |
| FR-AD1 — admin metrics | `admin`, observability adapters, web admin | aggregate queries, job/corpus/feedback data | RBAC and dashboard API tests |
| FR-AD2 — reindex and trace | `admin`, `ingestion`, worker | `ingestion_jobs`, snapshot state, traces, audit event | 202 job contract and trace visibility tests |
| FR-AD3 — flagged-answer queue | `admin`, `feedback`, `verification` | failed verification/down-vote query | review-queue inclusion tests |
| PRD §4/§13 safety and quality gates | `answering`, `verification`, corpus, evals | answer status, citations, evaluation reports | release gate blocks invalid content |

**Trade-off:** the mapping deliberately treats provenance and audit records as product data rather than logging. This adds relational writes, but is required to reproduce a clinical answer and satisfy FR-R4, FR-F3, and citation integrity.

## 4. Repository Structure and Dependency Rules

**Traceability:** PRD §8, §10–11, §16; ARC §§4–5, 16–18.

```text
evidence-platform/
  README.md
  AGENTS.md
  Makefile
  docker-compose.yml
  .env.example
  docs/
    architecture/
      MVP_ARCHITECTURE_BLUEPRINT.md
      TECHNICAL_DESIGN_DOCUMENT.md
      adr/
    api/
  apps/
    web/
      app/                    # pages, layouts, route handlers
      components/             # question, answer, evidence, source, admin UI
      lib/api/                # generated OpenAPI types and typed client only
      tests/
  backend/
    pyproject.toml
    alembic/
    src/evidence_platform/
      main.py
      api/
        v1/                   # routers only
        dependencies.py       # identity, policy, request context dependencies
        schemas/              # Pydantic DTOs; public contract only
      core/                   # config, errors, logging, security primitives
      modules/
        identity/
        corpus/
        retrieval/
        answering/
        verification/
        evidence/
        feedback/
        admin/
        ingestion/
      ports/                  # Protocols/interfaces and provider-neutral DTOs
      adapters/
        postgres/
        redis/
        qdrant/
        models/
        email/
      workers/
      db/
        models/               # SQLAlchemy mappings only
        repositories/         # adapter implementations only
        migrations_helpers/
    tests/
      unit/
      integration/
      contract/
      fixtures/
  integrations/
    ingestion_contract/
      v1.schema.json
      examples/
      importer.md
  evals/
    gold_sets/
    fixtures/
    retrieval/
    faithfulness/
    reports/
  infra/
    docker/
    deploy/
    scripts/
  scripts/
    import_manifest.py
    run_eval.py
    reindex.py
```

### 4.1 Import and ownership rules

1. `api` may import module application services and public DTOs, but never SQLAlchemy, Qdrant, Redis, or provider SDK code.
2. A domain module may import its own types and `ports`; it must not import FastAPI, SQLAlchemy mappings, or another module's private implementation.
3. Only `adapters/*` imports infrastructure SDKs. `adapters/postgres` implements repository ports; it is not a general data-access utility for modules.
4. `answering` coordinates modules through ports; it never issues SQL or Qdrant requests itself.
5. `verification` alone can transition an answer from `verifying` to `published`.
6. The web application uses generated OpenAPI types. It never embeds model/database/vector credentials or retrieval logic.
7. Cross-module use requires a small public facade or DTO; no inter-module HTTP requests and no generic `services/` directory.

**Trade-off:** this structure is stricter than a feature-folder shortcut, but preserves the architecture's one-process boundary while making costly-to-change adapters replaceable and testable.

## 5. Module Boundaries and Responsibilities

**Traceability:** PRD FR-A1–FR-AD3; ARC §5 module rules and ARC §16 repository guidance.

| Module | Public responsibility | May depend on | Must not own | PRD requirements satisfied |
|---|---|---|---|---|
| `identity` | user lifecycle, verification, sessions, roles, allowlist/status checks | repositories, email, Redis, security ports | retrieval, answer state, corpus mutation | FR-A1–FR-A3 |
| `corpus` | immutable source documents, revisions, sections, evidence units, chunks, display/retraction policy | corpus repositories | Qdrant authority, answer generation | FR-R2, FR-E1, FR-C2, FR-C3, FR-AD1–2 |
| `ingestion` | validate manifest, import revisions, enqueue/retry index work, publish snapshots | corpus facade, job/index ports | HTTP request-serving, user-facing answer flow | FR-E1, FR-C2, FR-AD1–2 |
| `retrieval` | normalize query, retrieve, fuse, rerank, select evidence IDs, persist deterministic trace input | corpus/read, embedding/reranker/vector ports | generated text, final publication state | FR-S2–S3, FR-R1–R4, FR-E2 |
| `evidence` | assemble parent context, evidence bundle, card DTOs, span/source-context resolution | corpus/read ports | LLM generation, source mutation | FR-E1–E2, FR-C1–C3 |
| `answering` | request lifecycle, idempotency, cache path, orchestration, safe result response | identity, retrieval, evidence, generation, verification ports | direct infrastructure access or publication decision | FR-S1, FR-R4, FR-C1–C3 |
| `verification` | validate draft schema/IDs/spans/numbers/support; calculate confidence; publish or abstain | corpus/evidence/answer repositories, verifier port | prompt composition, corpus mutation | FR-C1–C3; PRD §13 |
| `feedback` | validate visibility and persist answer/citation feedback | identity and feedback/answer read ports | retrieval ranking changes | FR-F1–F3 |
| `admin` | read diagnostics; enqueue reindex/publish; review queue; audit all mutations | policy, jobs, traces, corpus snapshots | direct worker implementation, non-audited writes | FR-AD1–AD3 |
| `ports` / `adapters` | stable provider and persistence boundaries | provider SDKs only in adapters | domain rules | all infrastructure-backed requirements |
| `apps/web` | UX for auth, question/filtering, answer/evidence, source context, feedback, admin | generated API client | clinical retrieval, provider credentials | FR-S1–S2, FR-E1–E2, FR-C1–C2, FR-F1–F2, FR-AD1–AD3 |

### 5.1 Required public interfaces

Public facades should remain small: `IdentityService`, `CorpusReader`, `CorpusImporter`, `RetrievalService`, `EvidenceService`, `AnswerOrchestrator`, `VerificationService`, `FeedbackService`, and `AdminService`. Public methods take immutable Pydantic/domain DTOs and return domain results; routers adapt those results to HTTP schemas. Each facade must have a fake implementation or injectable ports so unit tests do not require managed services.

**Trade-off:** the `evidence` and `verification` split introduces two modules instead of one RAG “helper,” but source selection/rendering and safety publication have different failure modes and need independent tests.

## 6. Database Schema Design

**Traceability:** PRD §10; ARC §§8–9, especially immutable revisions and canonical PostgreSQL ownership.

### 6.1 Database-wide conventions

- PostgreSQL 16+ is canonical. Use UUID primary keys generated by the application or database consistently; use `timestamptz` in UTC; use `jsonb` only for variable, versioned structures.
- Store source text in PostgreSQL, not Qdrant. Every character offset is UTF-8 code-point offset within an immutable text field; never normalize stored canonical text after a revision is published.
- Use `citext` for normalized email uniqueness. Hash secrets/tokens; never persist plaintext password, session, verification, IP address, or user-agent values.
- Define PostgreSQL enum types (or constrained text with one migration-controlled source) for roles, statuses, source states, job states, and verification states. Avoid unvalidated string literals in application code.
- Every migration is additive or an explicit, tested data migration. Never mutate a cited revision in place.

### 6.2 Core tables

| Table | Key columns and types | Constraints / behavior |
|---|---|---|
| `users` | `id uuid PK`, `email citext`, `password_hash text`, `role user_role`, `status user_status`, `email_verified_at timestamptz`, timestamps | unique email; roles `clinician|researcher|admin`; status `pending|active|suspended` |
| `sessions` | `id uuid PK`, `user_id uuid FK`, `token_hash bytea`, `expires_at`, `revoked_at`, `ip_hash bytea`, `user_agent_hash bytea`, timestamps | unique token hash; only unexpired/unrevoked sessions authenticate |
| `source_documents` | `id`, `source`, `source_key`, `pmid`, `pmcid`, `doi`, `canonical_url`, title/journal/authors/date, `mesh_terms jsonb`, `retraction_status`, `display_rights` | unique `(source, source_key)`; represents stable external identity, not mutable content |
| `corpus_snapshots` | `id`, `name`, `status`, `active_index_name`, `retrieval_profile_version`, `published_at` | exactly one active published snapshot per environment, enforced by partial unique index |
| `document_revisions` | `id`, `document_id FK`, `corpus_snapshot_id FK`, `revision_no`, `content_sha256`, `parser_version`, `source_artifact_uri`, `study_type`, `metadata jsonb`, `status` | unique `(document_id, revision_no)`; status `citation_not_ready|indexed|validated|published|superseded|rejected`; immutable once created |
| `document_sections` | `id`, `revision_id FK`, `section_path text[]`, `section_kind`, `ordinal`, `text`, `source_locator jsonb` | unique `(revision_id, ordinal)`; source text/locator authority |
| `evidence_units` | `id`, `section_id FK`, `ordinal`, `start_char`, `end_char`, `text`, `content_type`, `token_count` | bounded parent context; check offsets within parent section and text equals that slice at import time |
| `chunks` | `id`, `evidence_unit_id FK`, `ordinal`, `start_char`, `end_char`, `text`, `token_count`, model/version, `qdrant_point_id`, `index_status` | immutable child retrieval unit; unique `(evidence_unit_id, ordinal)` |
| `evidence_card_data` | `revision_id PK/FK`, `fields_json jsonb`, `extraction_version` | each visible field includes value, confidence, and locator; `fields_json` validated on write |
| `ingestion_runs` | `id`, external run ID, manifest version/checksum, started/finished timestamps, actor | one auditable import submission |
| `ingestion_jobs` | `id`, `run_id FK`, `revision_id FK`, job type, target index/version, `status`, lease owner/until, attempts, `next_attempt_at`, error | durable state; worker claims using `FOR UPDATE SKIP LOCKED` |
| `query_runs` | `id`, `user_id FK`, raw/normalized text, `filters jsonb`, `corpus_snapshot_id FK`, `status`, `latency_ms`, request ID, timestamps | immutable execution record; one idempotency record per accepted request |
| `retrieval_traces` | `id`, `query_run_id unique FK`, `normalization jsonb`, `config jsonb`, `model_versions jsonb`, `timings jsonb` | sufficient to replay logic with fixture providers |
| `retrieval_candidates` | `trace_id FK`, `chunk_id FK`, `stage`, `rank`, `score numeric`, `score_details jsonb` | unique `(trace_id, stage, rank)` and `(trace_id, stage, chunk_id)` |
| `answers` | `id`, `query_run_id unique FK`, `status`, `rendered_markdown`, `confidence`, model/prompt version, `verification_summary jsonb` | status `verifying|published|abstained|failed`; no published answer before verification invariant |
| `answer_claims` | `id`, `answer_id FK`, ordinal, text, `claim_type`, `is_material`, `verification_status` | unique `(answer_id, ordinal)`; material claim requires verified citation before publication |
| `citations` | `id`, `claim_id FK`, `chunk_id FK`, marker index, `span_start`, `span_end`, `verification_status`, `supporting_text_hash` | unique `(claim_id, chunk_id)`; span is chunk-relative; marker unique per answer via application transaction |
| `feedback` | `id`, `user_id FK`, `answer_id FK`, nullable `citation_id FK`, vote, reason code, free text, timestamp | if citation supplied, it must belong to a claim in the supplied answer |
| `audit_events` | `id`, actor ID, action, target type/ID, request ID, redacted metadata, timestamp | append-only audit for admin, identity, corpus publish/reindex, and session events |

### 6.3 State transitions and transaction boundaries

```text
revision: citation_not_ready -> indexed -> validated -> published -> superseded
                                      \-> rejected

job: queued -> leased -> succeeded
               |  \-> retry_wait -> leased
               \----> failed

query: received -> running -> answered | abstained | failed
answer: verifying -> published | abstained | failed
```

Use a single PostgreSQL transaction to persist the final query run outcome, answer, claims, citations, verification summary, and cacheable answer identity. The transaction performs the service-level publication invariant check immediately before changing `answers.status` to `published`. Qdrant updates are separate, idempotent projections and cannot publish a corpus snapshot by themselves.

### 6.4 Indexes and query patterns

- B-tree: all foreign keys; `query_runs(user_id, created_at DESC)`; `source_documents(pmid)`; `source_documents(doi)`; `document_revisions(status, study_type)`; `ingestion_jobs(status, next_attempt_at)`.
- GIN: `source_documents.mesh_terms`; selected document metadata; optional `chunks.search_tsv` for diagnostics/fallback evaluation only.
- `pg_trgm`: document title and DOI repair/deduplication lookup.
- Partial indexes: active published snapshot; published current revisions; failed verification answers; non-terminal jobs; feedback/down-vote review query.
- No vector similarity query is authoritative in PostgreSQL. Qdrant candidate IDs must be rehydrated and policy-checked from PostgreSQL before they become evidence.

**Trade-off:** immutable revisions consume more storage than in-place updates, but preserve what a user actually saw, enable retraction handling, and make citation spans reproducible.

## 7. Data Models and Internal Contracts

**Traceability:** PRD §§6, 9.2–9.9, 10–11, 13; ARC §§6–9, 10–11.

### 7.1 Canonical value objects

| Contract | Required fields | Rules |
|---|---|---|
| `QuestionRequest` | `question`, `filters`, `mode` | trim and Unicode-normalize only for validation; max 2,000 chars; filters allow study types and inclusive `year_from/year_to`; mode must be `standard` |
| `NormalizedQuery` | raw text, normalized text, expansion list, scope result, terminology version | retain raw and normalized values; rules must be deterministic and versioned |
| `SourceLocator` | `kind`, source location payload, locator version | accepted forms: JATS/XPath location or PDF page plus rectangle; no LLM-provided coordinates |
| `EvidenceRef` | `evidence_id`, `sentence_id`, chunk/revision IDs, study type, source locator | all IDs are server-generated and snapshot-bound |
| `EvidenceBundle` | snapshot ID, retrieval profile version, selected `EvidenceRef[]`, parent windows, token budget | 8–12 child chunks, no more than three per document; closed-world input to generation |
| `ClaimDraft` | text, evidence IDs, sentence IDs, claim type | LLM output only; unknown IDs are invalid; no citation marker/offset supplied by LLM |
| `VerificationResult` | structural/span/numeric/support checks, materiality, reasons | check results are persisted per claim/citation; semantic support is a screening signal, not clinical proof |
| `EvidenceCard` | source identity, study type, authors/year/journal, clinical fields, rights/link | fields must be extracted or canonical, never model-invented |

### 7.2 Ingestion manifest contract

Create `integrations/ingestion_contract/v1.schema.json`. It must require a schema version and, per source item, stable external IDs; canonical URL; display-rights and retraction/correction status; publication metadata; content checksum; parser name/version; upstream run ID; source artifact URI; ordered canonical sections; stable locators; table JSON plus linearized text; and confidence-scored structured clinical fields.

The importer validates schema and referential integrity before it creates a revision. It checks checksum/version consistency, orders sections deterministically, validates every character range, and rejects unsafe or incomplete records. A document that is valid enough to retain but lacks a displayable canonical span is persisted as `citation_not_ready`, queued for repair if appropriate, and excluded from retrieval and publication.

### 7.3 LLM structured-output contract

The generation port accepts `EvidenceBundle` and returns exactly this conceptual payload:

```json
{
  "status": "answer | insufficient_evidence",
  "direct_answer_claims": [
    {"text": "…", "evidence_ids": ["E07"], "sentence_ids": ["E07.S2"]}
  ],
  "evidence_summary_claims": [],
  "limitations_claims": [],
  "abstention_reason": null
}
```

The output parser rejects extra claim categories, unknown IDs, malformed schema, empty cited material claims, and text that exceeds configured answer budgets. A single repair call is permitted only for schema repair; it must reuse the exact same bundle and cannot introduce an additional generation attempt. The model never receives database credentials, tools, browsing, raw user identity, or permission to use knowledge outside the bundle.

### 7.4 Version tuple

Persist this tuple on each trace and answer: `corpus_snapshot_id`, parser version, chunker version, embedding model/version, Qdrant collection/schema version, retrieval profile version, reranker model/version, prompt schema version, LLM model/version, verifier version, terminology version. Cache keys include the canonical normalized query, filters, and all answer-affecting versions.

**Trade-off:** storing full version tuples is verbose, but avoids ambiguous regressions and is essential to the PRD's evaluation requirement.

## 8. API Specifications

**Traceability:** PRD FR-A1–FR-AD3 and PRD §11; ARC §§6, 10–11.

All API routes live under `/api/v1`, use JSON, return an `X-Request-ID`, and are described by FastAPI OpenAPI. Generate the TypeScript client/types in CI; do not manually duplicate DTOs. Next.js proxies `/api/*` same-origin to FastAPI.

### 8.1 Shared conventions

- Authentication is the `__Host-session` cookie. Unsafe routes require a CSRF token and valid same-origin `Origin`.
- `Idempotency-Key` is required on `POST /answers`; unique storage is scoped to the authenticated user and a request fingerprint. Reusing a key with a different fingerprint returns `409`.
- Client-visible errors use `{ "error": { "code", "message", "request_id", "details?" } }`. `details` contains field errors only for `422`; it never contains source text, provider errors, secrets, or trace data.
- An evidence insufficiency or verification failure is a successful, safe domain result: `200` with `status: "abstained"`, not a `5xx`.

### 8.2 Endpoint catalog

| Endpoint | Access | Request / response requirements | Error behavior |
|---|---|---|---|
| `POST /auth/signup` | public | email/password plus requested `clinician` or `researcher` role; create `pending` user and verification token | 422 invalid input; 409 existing account; rate-limited 429; `admin` cannot be self-selected |
| `POST /auth/verify-email` | verification token | consume/rotate token and mark verified | 400 invalid/expired token; no session creation |
| `POST /auth/login` | public | verified active account only; issue opaque session cookie | 401 generic invalid credentials; 403 pending/suspended; 429 throttle |
| `POST /auth/logout` | authenticated | revoke current session and clear cookie | idempotent 204 |
| `GET /me` | authenticated | user ID, role, status, verified state | 401 unauthenticated |
| `POST /answers` | active clinician/researcher | `QuestionRequest`; returns verified answer or abstention | 422 invalid/PHI/scope request; 403 gate; 429 quota; 503 only when safe result cannot be established |
| `GET /answers/{answer_id}` | owner/admin | canonical stored answer, citations, cards | 403 no ownership; 404 unknown/not visible |
| `GET /answers/{answer_id}/citations/{citation_id}/context` | owner/admin | source metadata, parent context, highlighted chunk span, external link | 403/404 prevent enumeration; no full text when rights disallow it |
| `POST /feedback` | active user; answer visible | answer/citation ID, vote, reason code, free text | 422 invalid linkage; 403 visibility |
| `GET /admin/stats` | admin | corpus, jobs, latency, volume, feedback summary | 403 non-admin |
| `GET /admin/query-runs/{id}/trace` | admin | sanitized normalization, candidates, context IDs, versions, verification results | 403/404 |
| `POST /admin/corpus/reindex` | admin | snapshot/index target; enqueue durable work | 202 with job ID; audit event mandatory |
| `POST /admin/corpus/publish/{snapshot_id}` | admin | publish only a pre-validated snapshot | 409 validation/index gate not met; audit event mandatory |

### 8.3 `POST /answers` contract

```json
{
  "question": "In HER2-low metastatic breast cancer, does T-DXd improve PFS versus chemotherapy?",
  "filters": {"study_types": ["rct", "meta_analysis"], "year_from": 2020},
  "mode": "standard"
}
```

```json
{
  "answer_id": "uuid",
  "query_run_id": "uuid",
  "status": "answered",
  "answer_blocks": [
    {"kind": "direct_answer", "text": "…", "citation_markers": [1]},
    {"kind": "limitations", "text": "…", "citation_markers": [1]}
  ],
  "confidence": "moderate",
  "citations": [
    {
      "marker": 1,
      "citation_id": "uuid",
      "document_revision_id": "uuid",
      "study_type": "rct",
      "span": {"start": 121, "end": 246}
    }
  ],
  "evidence_cards": [],
  "latency_ms": 0
}
```

For `status: "abstained"`, return `answer_id`, `query_run_id`, a controlled user-facing reason code/message, zero answer blocks containing clinical claims, optional safe filter suggestions, and `latency_ms`. Do not expose raw model text.

**Trade-off:** a single synchronous endpoint avoids asynchronous job and partial-state UI complexity. It relies on strict stage budgets and safe abstention when dependencies do not leave enough time for verification.

## 9. Service Layer Design

**Traceability:** PRD §§6, 8.3, 9.5–9.9; ARC §§5–7.

### 9.1 Answer orchestration

`AnswerOrchestrator.execute(actor, request, idempotency_key)` performs the following ordered workflow:

1. Validate request syntax and active user gate; run PHI and oncology-scope guard before any cache/model call.
2. Atomically enforce per-user and per-IP rate limits in Redis. On Redis uncertainty, fail closed for the rate-limit decision; a cache hit must not bypass the rate-limit gate.
3. Resolve idempotency. Return the stored terminal response for the same key/fingerprint; reject mismatched reuse.
4. Run deterministic normalization and persist a `query_run` in `running` state with request ID and start time.
5. Build the versioned cache key. If it maps to a published answer that still belongs to the active snapshot, load its canonical answer/citations from PostgreSQL and return it.
6. Call `RetrievalService`; it obtains dense and sparse candidates concurrently, fuses/reranks them, and saves all stages in a trace.
7. Rehydrate selected candidates through `CorpusReader`; drop any record no longer published, retrievable, displayable for context, non-retracted, oncology-scoped, and in the expected snapshot.
8. Call `EvidenceService` to assemble the bounded `EvidenceBundle`. If retrieval/coverage policy fails, persist a safe abstention and return it.
9. Call the LLM generation port once, or once more solely to repair malformed structured output. Pass the same evidence bundle on both calls.
10. Call `VerificationService`. It validates IDs, snapshot membership, sentence/span resolution, numeric support, semantic support signal, conflict/limitation policy, and calibrated confidence.
11. In one transaction, persist a published answer and cache reference if every material claim passes; otherwise persist an abstention. Only after commit may Redis receive the answer ID cache entry.

### 9.2 Ports

| Port | Operations | Adapter behavior |
|---|---|---|
| `UserRepository`, `SessionRepository` | identity reads/writes and revocation | PostgreSQL transactions |
| `CorpusRepository`, `AnswerRepository`, `TraceRepository`, `JobRepository` | canonical persistence | PostgreSQL; no business decisions in repository |
| `VectorSearchClient` | filtered dense/sparse top-K and idempotent upsert/delete by collection | Qdrant only; returns IDs/scores/payload needed for filter diagnostics |
| `EmbeddingClient`, `RerankerClient`, `LLMClient`, `EntailmentClient` | inference calls with explicit timeouts/version metadata | hosted provider adapters; never silently change model/version |
| `CacheClient`, `RateLimitClient`, `LockClient` | short-lived reads/writes, atomic quotas, locks | Redis; cache/lock loss cannot affect canonical truth |
| `EmailClient` | verification/waitlist emails | transactional provider with idempotent send key |
| `Clock`, `IdGenerator` | deterministic tests | real system implementations in production; fakes in unit tests |

### 9.3 Timeouts, retry, and circuit policy

Use the online budgets from ARC §6 as a hard envelope: auth/cache 100 ms; normalize/scope 100 ms; embedding/retrieval 1.0 s; fusion/rerank 1.5 s; hydration/context 500 ms; generation 7 s; verification/persist 2.5 s. Each outgoing provider call receives a shorter explicit timeout than the remaining request deadline. Retry only one repairable structured-output parse error. Do not retry a timed-out generation or silently route to another model. On retrieval, provider, or verifier failure, return/persist `abstained` when the system can safely do so; return `503` only when canonical persistence or the safe response path itself fails.

**Trade-off:** one bounded repair increases success on schema glitches without turning verification failure into repeated generation. It preserves predictable latency and avoids sampling a different clinical response.

## 10. RAG Pipeline Design

**Traceability:** PRD §§6, 9.1–9.9, 13–14, 16; ARC §§6–8, 18–19.

### 10.1 Offline corpus and indexing pipeline

1. Receive an upstream manifest conforming to `v1.schema.json`; record its checksum and external ingestion run.
2. Deduplicate source identity, create a new immutable `document_revision`, and preserve the prior revision.
3. Apply profile-specific validation: research papers are JATS-first with scholarly PDF fallback; guidelines preserve each recommendation, evidence-strength label, population, and surrounding condition/algorithm context.
4. Persist canonical sections, tables, source locators, structured clinical field confidence, bounded evidence units, and child chunks.
5. Create durable PostgreSQL index jobs. Worker handlers claim with `FOR UPDATE SKIP LOCKED`, lease, retry with bounded attempts/backoff, and update error state.
6. Generate BGE-M3 dense and learned-sparse vectors; upsert child chunks to a versioned Qdrant collection with indexed payload filters.
7. Run index smoke tests and versioned retrieval evaluation. Only then let an admin publish the snapshot, atomically switching the active-index configuration.
8. Reconcile retractions on a schedule. Update Qdrant payload and invalidate versioned caches; the online hydration check remains the final hard stop.

### 10.2 Chunking rules

- Segment structured papers by IMRaD/table boundaries and guidelines by atomic recommendation units before token splitting.
- Use parent evidence units of normally 600–1,200 tokens. Use child retrieval chunks of normally 256–320 tokens with about 15% overlap.
- Never split a table row, statistical result sentence, or guideline recommendation. Store linearized tables for retrieval and structured table JSON for cards.
- Retrieve children; expand only selected children to their bounded parent windows. Do not send an unbounded Results section or entire document to the model.
- Index figure captions plus adjacent results text. Do not infer clinical information from figure images in MVP; rank supplements lower by default.

### 10.3 Online retrieval profile v1

1. Normalize Unicode/whitespace and run curated oncology acronym, generic/brand, spelling, and MeSH synonym rules. Keep raw and normalized forms.
2. Return a controlled abstention for non-oncology scope. Reject likely-PHI input before provider calls. Apply user filters plus snapshot, retraction, display-rights, retrievability, and oncology scope before vector search.
3. Embed the normalized query once. Query dense top 100 and sparse top 100 concurrently in Qdrant with identical filters.
4. Apply application-level Reciprocal Rank Fusion with initial `k=60`; persist each leg's rank and the fused score. Do not compare raw dense/sparse scores directly. The versioned `RetrievalProfile` must support a disabled-by-default weighted-fusion experiment (`dense_weight`, `sparse_weight`) so FR-R1's configurable fusion requirement can be evaluated without changing the production RRF default. A weighted profile cannot be promoted without an evaluation report and ADR.
5. Rerank only the fused top 50, in one bounded batch with truncated passages. Preserve the rerank score/model/version.
6. Select 8–12 chunks with MMR de-duplication, maximum three chunks per document, Methods/Results balance, intact guideline recommendations, a bounded parent context, and transparent secondary study-type tie-breaking.

### 10.4 Verification and abstention

For each claim, verification must: validate evidence/sentence IDs are in the bundle; confirm the chunk/revision/snapshot remains valid; resolve server-owned sentence character spans; compare every number, percentage, CI, dosage, and sample size after safe normalization; run entailment/semantic-support screening; and record pass/fail reasons. A material failure causes a controlled abstention unless a safe non-material presentation-only fragment can be removed without altering a clinical claim.

Return abstention for out-of-scope/PHI input, weak retrieval, no displayable evidence, material unsupported claims after the one repair, material conflict that cannot be transparently reconciled, or provider/verification state that leaves no safe answer. Confidence labels are calibrated system labels based on retrieval strength, diversity, source metadata, verification pass rate, coverage, and conflict signals; they are not LLM self-assessments or recommendations.

**Trade-off:** BGE-M3 provides one dense+sparse model and fewer operational paths, while MedCPT stays an evaluation challenger. Do not ship an embedding switch on intuition; promote it only through the versioned evaluation gate.

## 11. Authentication and Authorization Design

**Traceability:** PRD FR-A1–FR-A3; ARC §§10–11, 13.

- Signup validates email/password and requested `clinician`/`researcher` role, hashes with maintained Argon2id tooling, creates a `pending` user, stores only a hashed one-time verification token, and sends a transactional verification email. `admin` is provisioned only through a controlled bootstrap/operations procedure.
- Verification establishes `email_verified_at` but does not grant access. The user must also be `active` through the clinician/allowlist gate.
- For MVP, status changes are performed by an audited deployment-local admin operation rather than an unscoped public user-management API. It updates only `users.status`, records `audit_events`, and is covered by the same central policy checks; a user-management screen is not in PRD scope.
- Login verifies password, role/status, and rate limit; it creates a cryptographically random opaque session token, stores only its hash, and sets `__Host-session` with `HttpOnly`, `Secure`, `SameSite=Lax`, host-only path `/` attributes.
- Rotate session on login and privilege/status-changing events. Logout/revocation invalidates the server record and clears the cookie.
- FastAPI dependencies derive an `Actor` once per request and enforce active status, role, CSRF/Origin validation for unsafe routes, and ownership checks. The web app never receives a bearer token.

| Actor | Answers/citations | Feedback | Admin metrics/traces | Reindex/publish |
|---|---|---|---|---|
| Active clinician/researcher | own records only | own visible answer/citation only | no | no |
| Pending/suspended user | no | no | no | no |
| Admin | any record needed for administered review | may inspect | yes | yes, with audit |
| Anonymous | signup/login/verification only | no | no | no |

**Trade-off:** opaque sessions require one database lookup rather than stateless browser JWT validation, but enable immediate revocation and clear ownership/RBAC behavior for a clinician-gated beta.

## 12. Error Handling Strategy

**Traceability:** PRD §§6, 13–14; ARC §§6, 10, 13, 18.

| Category | HTTP / answer status | Client treatment | Log/audit treatment |
|---|---|---|---|
| validation, unsupported `mode`, likely PHI | `422` | explain field/safe-entry issue; never echo suspected PHI | request ID, redacted reason code |
| unauthenticated / forbidden | `401` / `403` | sign-in or access-state message | security event; audit admin denial as appropriate |
| rate limit | `429` | retry-after indication | quota dimension and request ID only |
| missing/hidden resource | `404` | generic not-found | do not reveal whether another user's record exists |
| idempotency key conflict | `409` | require a new key for changed request | request fingerprint hashes only |
| insufficient evidence / failed grounding / external inference safe failure | `200`, `status: abstained` | controlled explanation and optional safe filter suggestion | persisted run, trace, structured abstention reason |
| transient infrastructure where abstention can be persisted | `200`, `status: abstained` | no partial answer | dependency category/timeout; error tracking scrubbed |
| safe persistence path unavailable | `503` | generic retryable service message | high-severity error with request ID; no model/evidence dump |
| worker job failure | no browser response | admin sees retry/failed state | job error category, attempts, lease history |

Domain exceptions must be typed (`ValidationError`, `PolicyDenied`, `EvidenceInsufficient`, `VerificationFailed`, `DependencyUnavailable`, `Conflict`, `NotFound`) and translated only in the API layer. Do not catch all exceptions in modules and turn them into generic success responses. Worker handlers classify retryability explicitly; malformed manifests and invalid locators are terminal/non-retryable, while provider timeouts may retry according to job policy.

**Trade-off:** using `200` for abstentions distinguishes an honest product result from an outage. It requires the UI and metrics to treat `answered` and `abstained` as separate successful terminal states.

## 13. Logging, Observability, and Auditability

**Traceability:** PRD FR-R4, FR-F3, FR-AD1–AD3, §14; ARC §§6, 9, 12–13, 18.

### 13.1 Structured telemetry

Every API request logs: timestamp, environment, deployment version, request ID, route/method/status, authenticated actor ID hash, role, response latency, cache/idempotency outcome, and safe error category. Answer runs additionally record stage timings, snapshot/index/profile/model/verifier versions, candidate counts, selected evidence IDs, verification aggregate, answer/abstention status, and cost/token metrics where provider terms allow. Store reproducibility details in PostgreSQL trace tables; emit only redacted operational summaries to logs/tracing.

Never log raw question text, full prompt, full evidence bundle, session tokens, passwords, verification tokens, email, IP address, user agent, source artifacts, or provider secrets. Hash/pseudonymize where an identifier is essential. Error tracker integrations must run a scrubber before export.

### 13.2 Metrics and alerts

- Request count/error rate by route and error class; authentication/rate-limit events.
- p50/p95 end-to-end and per-stage timings against 8 s/15 s gates.
- cache hit rate, idempotency replay rate, provider timeout/error rate, token/cost per answer.
- retrieval stage counts, Recall@10/20/50, MRR, nDCG@10, rerank precision, profile/model ablations.
- citation coverage/validity/span resolution, numeric-verification failures, abstention rate/reason, faithfulness, fabricated-reference count.
- ingestion throughput, `citation_not_ready` count, job age/retries/failures, snapshot publish state, late-retraction reconciliation lag.
- feedback votes/reasons/support agreement and flagged-answer queue depth.

Create OpenTelemetry-compatible spans for `auth`, `rate_limit`, `cache`, `normalize`, `embed`, `dense_search`, `sparse_search`, `rrf`, `rerank`, `hydrate`, `assemble`, `generate`, `verify`, `persist`, and worker job stages. Admin views read sanitized canonical data and aggregates; they do not query application logs as the system of record.

**Trade-off:** retaining detailed traces costs storage, but is necessary for reproducible retrieval evaluation and a clinically useful review workflow. Redaction limits debugging detail by design to honor the no-PHI policy.

## 14. Deployment Design

**Traceability:** PRD §8, §14, §16; ARC §§4, 12, 14, 17.

### 14.1 Runtime topology

- CDN/WAF/TLS terminates the same-origin web domain.
- Next.js serves the browser UI and proxies API calls; it has no database, Qdrant, Redis, model, or email secrets.
- One FastAPI image runs as API role with health checks and bounded autoscaling only after load-test evidence.
- The same image runs as one worker role initially for manifests, embeddings, indexing, reindexing, and retraction reconciliation.
- Managed PostgreSQL provides encrypted storage and point-in-time recovery; managed Redis uses TLS/private access; managed Qdrant uses TLS/API key/restricted network; approved model providers are accessed through allowlisted outbound connections.

### 14.2 Environments and release flow

| Environment | Required isolation | Purpose |
|---|---|---|
| Local | Docker Compose PostgreSQL/Redis/Qdrant; fake model adapters; licensed fixtures | fast deterministic development/tests without paid calls |
| Staging | separate database, Redis namespace, Qdrant collection, credentials, snapshot, and allowlist | deployment, integration, and controlled evaluation |
| Private beta production | separate managed state, secrets, published corpus, clinician allowlist | real gated use only after security/eval gates |

Release sequence: apply migration -> deploy worker-compatible image -> import/reindex into a new versioned collection -> run smoke and evaluation gate -> publish snapshot -> deploy API/web feature -> observe canary metrics. Rollback of retrieval/index behavior is an active-snapshot configuration change; do not destructively overwrite a collection or revision.

Infrastructure-as-code should cover environment manifests, database migration invocation, managed service configuration, secret references, network policy, and backups once deployment is stable. Start with PaaS and managed services; do not introduce Kubernetes, a GPU fleet, or a message broker.

**Trade-off:** managed services increase vendor dependence but focus the three-person team on evidence quality and avoid operating stateful clusters before the MVP validates demand.

## 15. Testing and Evaluation Strategy

**Traceability:** PRD §4, §14, §16; ARC §§14, 17–18.

| Layer | Scope | Required examples |
|---|---|---|
| Unit | pure module logic with fakes | normalization, filters, RRF, MMR, chunk boundaries, offsets, numeric matching, confidence, policy/RBAC, state transitions |
| Repository integration | PostgreSQL/Qdrant/Redis adapters and migrations | immutable revision, job lease/retry, trace persistence, answer publication invariant, cache/idempotency semantics |
| Provider contract | model/provider boundaries using deterministic fixtures | malformed draft, unknown IDs, timeout, reranker batching, version capture |
| API/OpenAPI contract | routers and generated web client | schema generation diff gate, status/error shapes, cookie/CSRF behavior, ownership/non-enumeration |
| E2E | browser journeys with fixture corpus | signup -> gate -> login -> answer/abstention -> span context -> feedback -> admin trace |
| Retrieval evaluation | versioned gold set | Recall@10/20/50, MRR, nDCG@10, post-rerank precision; dense/sparse/hybrid/reranker/model ablations |
| Answer evaluation | versioned expected evidence/claims | citation coverage, validity, numeric fidelity, faithfulness, abstention correctness, clinician rubric |
| Performance/resilience | controlled load and fault injection | stage budget, concurrent requests, cache, provider timeout, Qdrant/Redis uncertainty, safe abstention |
| Security/operations | security test plan | authz matrix, CSRF, injection payloads, log redaction, dependency scan, backup/restore rehearsal |

Fixtures must include answerable questions, conflicting evidence, deliberately weak retrieval, retracted/rights-blocked sources, non-oncology and suspected-PHI input, malformed upstream manifests, bad provider outputs, and one known hard question per important oncology subdomain. Gold sets and reports are versioned and immutable once used as a release comparator.

Promotion blocks: any fabricated citation; unresolved displayed span; any material claim without a verified citation; failure of the 95% citation coverage, 0.90 faithfulness, or latency targets; missing evaluation/ADR for a corpus/model/prompt/profile change. Human clinical spot checks remain required because automated entailment is a screen, not proof of medical correctness.

**Trade-off:** test fixtures and evaluation reports take upfront effort, but manual demos cannot catch retrieval regressions hidden by fluent answers.

## 16. Security Considerations

**Traceability:** PRD §13 and Appendix B; ARC §§11, 13, 19–20.

| Threat area | Required control | Implementation boundary |
|---|---|---|
| Clinical safety | closed evidence bundle; citation/numeric/span verification; retraction hard-stop; scope/coverage abstention; disclaimer and conflict/limitation display | `answering`, `verification`, `corpus`, web |
| PHI/privacy | warning and server-side detection before providers; no raw query logging; no user URL fetch; legal/data processing review before beta | API dependency, logging, provider adapter |
| Credentials | Argon2id; opaque revocable session; verification; CSRF/Origin; rate limits; secure cookie | `identity`, web proxy |
| Authorization | central role/status/ownership dependencies; audited admin actions | `identity`, `admin`, API dependencies |
| Input/output | Pydantic bounds, parameterized SQL, output encoding, CSP/secure headers, no permissive CORS | API/web/core |
| Corpus supply chain | allowed source classes, checksum/parser version, display/retraction state, immutable revision, no arbitrary URL ingestion | `ingestion`, `corpus` |
| Prompt injection | evidence delimited as data; no model tools/browsing/DB access; closed schema; no instruction authority from corpus | generation adapter, `verification` |
| Secrets/network | secret manager/environment injection, TLS to all managed services, least privilege, rotation plan, network restriction | deployment/infra |
| Availability/cost | quota, idempotency, explicit timeouts, one bounded schema repair, circuit to abstention, backup/PITR restore test | `answering`, Redis, infra |

This design is not a claim of HIPAA, SOC 2, or EHR-production compliance. It intentionally forbids PHI/EHR data in MVP. Formal retention, BAA/data-processing, licensing, penetration-test, incident-response, and compliance decisions are launch gates, not features to improvise in code.

**Trade-off:** rejecting suspected PHI can reject legitimate but ambiguously phrased questions; this is preferable to sending possible patient data to a hosted provider under the MVP's stated scope.

## 17. Implementation Roadmap

**Traceability:** PRD §16; ARC §17.

| Increment | Build scope | Exit gate | Main requirements |
|---|---|---|---|
| 0 — foundations/eval | repo, Compose, config, migrations, logging/request IDs, fake adapters, fixture corpus, gold-set format | developer starts dependencies, imports fixtures, runs deterministic retrieval/eval with no paid model calls | PRD §14, FR-R4 |
| 1 — corpus/source readiness | manifest schema, immutable corpus schema, importer, locators, context API, retraction/rights, jobs, admin job view | every fixture citation resolves through chunk/section to exact span; invalid rights/offsets never publish | FR-E1, FR-C2–C3, FR-AD1–2 |
| 2 — retrieval | normalization, Qdrant projection worker, dense+sparse, RRF, filters, traces, retrieval eval/admin trace | baseline Recall/nDCG measured; every candidate/filter/rank explained; no LLM required | FR-S2–S3, FR-R1–R4 |
| 3 — rerank/evidence | inference adapter, cross-encoder, parent expansion, MMR, caps/balance, cards, timings | reranker improves agreed metric within budget; sources inspectable | FR-R3, FR-E1–E2 |
| 4 — grounded answers | structured LLM, claim/citation persistence, ID/numeric/span/support checks, confidence, abstention, answer UI | zero fabricated references in release suite; bad provider fixture abstains safely | FR-C1–C3, PRD §13 |
| 5 — access/operations | auth/gate/RBAC, quotas/cache, feedback, admin stats/review, audit, tracking, restore rehearsal | controlled load meets product quality/latency gates and mutations are audited | FR-A1–A3, FR-F1–F3, FR-AD1–AD3 |
| 6 — clinician calibration | review workflow, feedback triage, experiments, reconciliation, alert thresholds, weekly reports | design partners validate source span, cards, and abstention usefulness | PRD §14–15 |

No increment may absorb a deferred capability. A change from the above build order requires an ADR that names product/architecture traceability, risk, evaluation plan, migration impact, and rollback plan.

**Trade-off:** the staged order delays polished UI and access features until the evidence chain is demonstrably safe, but prevents a convincing interface from masking missing provenance or weak retrieval.

## 18. Module Acceptance Criteria

**Traceability:** PRD FR-A1–FR-AD3, §4, §13–16; ARC §§5–13, 17–18.

| Module | Requirements / architecture | Acceptance criteria |
|---|---|---|
| `identity` | FR-A1–A3; ARC §§5, 11 | AC-ID-1: only verified active users can create answers. AC-ID-2: password/session/verification token plaintext is never persisted or logged. AC-ID-3: role/status/ownership matrix is fully tested. AC-ID-4: unsafe cookie requests require CSRF and Origin checks. |
| `corpus` | FR-R2, FR-E1, FR-C2–C3; ARC §§5, 8–9 | AC-CO-1: a cited span resolves to immutable chunk then section text. AC-CO-2: retracted, unlicensed-for-display, unpublished, and `citation_not_ready` revisions cannot hydrate into context. AC-CO-3: an update creates a new revision, never changes cited text. |
| `ingestion` | FR-E1, FR-AD1–2; ARC §8 | AC-IN-1: schema/locator/checksum failures are classified and observable. AC-IN-2: jobs survive worker restart and retry idempotently. AC-IN-3: snapshot publish is impossible before index/evaluation validation. |
| `retrieval` | FR-S2–S3, FR-R1–R4, FR-E2; ARC §§6–7 | AC-RE-1: dense and sparse top-100 use identical pre-filters. AC-RE-2: trace persists leg/fused/reranked ranks, scores, config, models, and timings. AC-RE-3: reranker never sees more than 50 candidates. AC-RE-4: selected context honors diversity/per-document/section constraints. |
| `evidence` | FR-E1–E2, FR-C1–C3; ARC §§7–8 | AC-EV-1: evidence cards use only canonical/extracted fields and disclose `Not extracted` when confidence is low. AC-EV-2: citation context returns highlighted server-resolved span or no displayable text. AC-EV-3: marker ordering maps deterministically to visible cards. |
| `answering` | FR-S1, FR-R4, FR-C1–C3; ARC §6 | AC-AN-1: same user/key/fingerprint returns same stored terminal response; mismatch conflicts. AC-AN-2: no model text reaches the API response before verification. AC-AN-3: every terminal request persists a run/trace/outcome or returns 503 if it cannot do so safely. |
| `verification` | FR-C1–C3; PRD §13; ARC §7 | AC-VE-1: unknown evidence/sentence ID, snapshot mismatch, unresolved span, or unsupported material number prevents publication. AC-VE-2: all material published claims have at least one `verified` citation. AC-VE-3: failure fixtures produce controlled abstention, not partial text. |
| `feedback` | FR-F1–F3; ARC §5 | AC-FB-1: answer and citation feedback validates ownership/visibility and citation-to-answer linkage. AC-FB-2: a feedback item can be joined to its exact trace without duplicating mutable trace data. |
| `admin` | FR-AD1–AD3; ARC §§5, 10, 12 | AC-AD-1: non-admin callers cannot enumerate traces/jobs. AC-AD-2: reindex returns a durable job ID and publish rejects unvalidated snapshots. AC-AD-3: every mutation has an append-only audit event. AC-AD-4: review queue includes failed verification and down-voted answers. |
| `ports/adapters` | ARC §§4–5, 14 | AC-PA-1: domain modules compile/test with fake ports. AC-PA-2: Qdrant/Redis/provider failures cannot bypass PostgreSQL authority or publication invariant. AC-PA-3: provider/model versions are surfaced in traces. |
| `apps/web` | FR-S1–S2, FR-E1–E2, FR-C1–C2, FR-F1–F2, FR-AD1–AD3; ARC §4 | AC-WE-1: answer page presents answer, confidence, limitations, inline markers, and evidence cards without inventing data. AC-WE-2: clicking a marker opens exact permitted source context. AC-WE-3: no infrastructure/model credential is exposed to browser bundles. AC-WE-4: admin screens are absent/inaccessible for non-admin sessions. |

**Trade-off:** module-level gates create more test artifacts than a single end-to-end checklist, but localize failures so coding agents can implement and verify each boundary independently.

## 19. Final Implementation Guardrails

**Traceability:** PRD §§4–5, 13–16; ARC §§1–2, 15, 19–20.

1. Do not add a product feature merely because an implementation library makes it easy. The PRD scope is the product boundary.
2. Do not add a service, queue, cluster, agent workflow, vector authority, or client-side credential path without an ADR showing that the architecture requires it.
3. Make every answer-affecting setting versioned and evaluated before promotion.
4. Prefer abstention to a less-grounded answer, and canonical PostgreSQL evidence over a vector payload.
5. Treat every citation as a chain: `answer claim -> verified citation -> immutable chunk span -> document section -> immutable revision -> corpus snapshot`.

Following these guardrails keeps implementation aligned to the intended MVP: a clinician can ask an oncology question, receive a fast answer whose material claims link to exact evidence passages, and independently decide whether to trust it.

**Trade-off:** these guardrails constrain seemingly useful shortcuts—such as rendering unverified model text or using vector payloads as source text—but those shortcuts directly undermine the trust and reproducibility the MVP is intended to validate.

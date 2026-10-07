# 🧬 Indence — Evidence-First Clinical Intelligence Platform

> **AI-powered medical evidence retrieval and clinical decision-support platform for oncology.**

**Indence** is an evidence-first clinical intelligence platform designed to help healthcare professionals find, retrieve, rank, and understand high-quality medical evidence from trusted research sources.

Unlike a general-purpose medical chatbot, Indence is designed around **evidence retrieval, source grounding, intelligent ranking, and citation-backed answers**.

The goal is simple:

> **Ask a clinical question → retrieve the most relevant evidence → rank the strongest sources → generate a grounded answer with citations.**

---

## 🚀 Why Indence?

Medical knowledge is continuously expanding. Thousands of research papers, clinical studies, systematic reviews, meta-analyses, and guidelines are published across different sources.

Finding the **right evidence at the right time** can be difficult.

Traditional search systems often return:

- Too many irrelevant papers
- Duplicate results
- Low-quality evidence
- Long documents requiring manual screening
- Results that are difficult to compare
- Information without sufficient clinical context

Indence addresses this problem by combining **information retrieval + semantic search + reranking + evidence-grounded generation** into a single pipeline.

---

# 🎯 Core Objectives

Indence is designed to:

- 🔎 Retrieve relevant medical literature
- 🧠 Understand clinical queries semantically
- 📚 Search across structured medical evidence
- ⚡ Combine lexical and semantic retrieval
- 🏆 Rerank retrieved evidence by relevance
- 🧩 Expand retrieved chunks to their parent context
- 🤖 Generate answers grounded in retrieved evidence
- 🔗 Provide source-level citations
- 📊 Evaluate retrieval quality using curated gold sets
- 🏥 Focus on evidence-based oncology use cases

---

# 🏗️ System Architecture

```text
                         ┌──────────────────────┐
                         │    Clinical Query    │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   Query Processing   │
                         └──────────┬───────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
          ┌──────────────────┐             ┌──────────────────┐
          │ Semantic Search  │             │   BM25 Search    │
          │    / Dense       │             │     / Sparse     │
          └────────┬─────────┘             └────────┬─────────┘
                   │                                │
                   └───────────────┬────────────────┘
                                   ▼
                         ┌──────────────────────┐
                         │    Result Fusion     │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   Parent Expansion   │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   Neural Reranker    │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   Top Evidence       │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │ Evidence-Grounded    │
                         │      Generation      │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │ Answer + Citations  │
                         └──────────────────────┘
```

---

# 🔬 Retrieval Pipeline

Indence follows a multi-stage retrieval architecture.

### 1. Query Understanding

The clinical question is processed to identify the important concepts and retrieval intent.

Example:

```text
"What is the first-line treatment for HER2-positive metastatic breast cancer?"
```

The system needs to understand concepts such as:

- Cancer type
- Biomarker
- Disease stage
- Treatment setting
- Treatment line
- Clinical intent

---

### 2. Dense Retrieval

Semantic retrieval is used to find documents that are conceptually related to the query even when exact keywords do not match.

This helps retrieve evidence using **meaning rather than only keyword overlap**.

---

### 3. Sparse Retrieval

Traditional lexical retrieval such as **BM25** complements semantic search.

This is especially useful for:

- Drug names
- Gene mutations
- Biomarkers
- Clinical terminology
- Dosages
- Trial identifiers
- Exact medical phrases

---

### 4. Hybrid Retrieval

Dense and sparse retrieval results are combined.

```text
Dense Retrieval
       +
BM25 Retrieval
       ↓
Result Fusion
       ↓
Candidate Evidence
```

Hybrid retrieval improves robustness by combining **semantic similarity with exact lexical matching**.

---

### 5. Parent Expansion

Retrieved child chunks can be expanded back to their relevant parent sections.

This helps preserve context and reduces the risk of answering from an isolated sentence.

---

### 6. Neural Reranking

Retrieved candidates are reranked using a dedicated reranking model.

Instead of relying only on vector similarity, the reranker evaluates:

```text
Clinical Query
      +
Retrieved Evidence
      ↓
Relevance Score
      ↓
Ranked Evidence
```

The highest-quality evidence is then passed to the generation layer.

---

### 7. Evidence-Grounded Generation

The language model receives the selected evidence and generates an answer grounded in the retrieved sources.

The system is designed to reduce unsupported generation by keeping the generation layer dependent on retrieved evidence.

---

# 📚 Evidence Sources

Indence is designed around high-quality medical literature and evidence sources, including:

- PubMed / PubMed Central
- Peer-reviewed research
- Randomized controlled trials
- Systematic reviews
- Meta-analyses
- Clinical guidelines
- Oncology evidence
- Structured medical literature metadata

The project includes a dedicated:

```text
pmc-pipeline/
```

for processing medical literature from the PubMed Central ecosystem.

---

# 🧠 Evidence Hierarchy

Indence prioritizes evidence quality rather than simply returning the most textually similar document.

The retrieval system can prioritize evidence such as:

```text
Clinical Guidelines
       ↓
Systematic Reviews
       ↓
Meta-Analyses
       ↓
Randomized Controlled Trials
       ↓
Observational Studies
       ↓
Other Relevant Literature
```

The exact ranking strategy can be adapted according to the clinical use case.

---

# 🧩 Project Structure

```text
Indence_v3_1/
│
├── backend/
│   └── Backend services and API components
│
├── frontend/
│   └── User-facing application
│
├── pmc-pipeline/
│   └── Medical literature ingestion and processing
│
├── evals/
│   └── Evaluation framework
│       └── gold_sets/
│           └── Curated evaluation datasets
│
├── docs/
│   └── Project documentation
│
├── scripts/
│   └── Utility and automation scripts
│
├── docker-compose.yml
│   └── Container orchestration configuration
│
├── start_indence.bat
│   └── Windows startup script
│
└── README.md
```

---

# ⚙️ Technology Stack

The project is built around a modern AI retrieval architecture.

### AI / NLP

- Large Language Models
- Embeddings
- Semantic Search
- RAG
- Neural Reranking
- Natural Language Processing

### Information Retrieval

- Dense Retrieval
- Sparse Retrieval
- BM25
- Hybrid Search
- Result Fusion
- Parent-Child Retrieval
- Reranking

### Medical Data

- PubMed
- PubMed Central
- Medical research papers
- Clinical guidelines
- Oncology literature

### Infrastructure

- Python
- REST APIs
- Docker
- Docker Compose
- Vector Search
- Backend Services
- Web Frontend

---

# 📊 Evaluation

A medical retrieval system should not only work on a few examples.

Indence therefore includes an evaluation layer:

```text
evals/
└── gold_sets/
```

The evaluation framework is intended to measure retrieval and answer quality using curated clinical queries and expected evidence.

Important evaluation dimensions include:

### Retrieval Quality

- Precision@K
- Recall@K
- MRR
- NDCG
- Hit Rate

### Evidence Quality

- Source relevance
- Evidence coverage
- Citation correctness
- Evidence completeness

### Generation Quality

- Groundedness
- Faithfulness
- Answer relevance
- Citation support

---

# 🔐 Safety & Clinical Scope

Indence is an **evidence retrieval and decision-support research platform**, not a replacement for qualified medical professionals.

The system should not be used as an autonomous diagnostic or treatment system.

Medical decisions should always be made by appropriately qualified healthcare professionals using validated clinical guidelines, patient-specific information, and professional judgment.

---

# 🛠️ Local Development

## 1. Clone the repository

```bash
git clone https://github.com/Cosmicgod5151/Indence_v3_1.git

cd Indence_v3_1
```

## 2. Configure environment variables

Create the required environment configuration according to the backend and pipeline requirements.

Example:

```env
API_KEY=your_api_key
DATABASE_URL=your_database_url
VECTOR_DB_URL=your_vector_database_url
LLM_API_KEY=your_llm_api_key
```

> Never commit API keys, passwords, tokens, or other secrets to GitHub.

---

## 3. Run with Docker

```bash
docker compose up --build
```

This starts the services defined in:

```text
docker-compose.yml
```

---

## 4. Windows Startup

For Windows development, the repository also contains:

```text
start_indence.bat
```

which can be used as the project startup entry point.

---

# 🔄 End-to-End Workflow

```text
Medical Literature
       ↓
Data Ingestion
       ↓
Document Processing
       ↓
Metadata Extraction
       ↓
Chunking
       ↓
Embedding
       ↓
Vector / Sparse Index
       ↓
User Clinical Query
       ↓
Hybrid Retrieval
       ↓
Result Fusion
       ↓
Parent Expansion
       ↓
Reranking
       ↓
Top Evidence
       ↓
LLM Generation
       ↓
Citations
       ↓
Clinical Evidence Answer
```

---

# 💡 Example Use Case

### Clinical Question

```text
What are the current first-line treatment options for
HER2-positive metastatic breast cancer?
```

### Indence Pipeline

```text
Clinical Query
      ↓
Query Processing
      ↓
Dense + BM25 Retrieval
      ↓
Candidate Evidence
      ↓
Reranking
      ↓
High-Quality Oncology Sources
      ↓
Evidence-Grounded Answer
      ↓
Citations
```

The objective is not simply to produce a fluent answer.

The objective is to produce an answer where the **important claims can be traced back to relevant medical evidence**.

---

# 🌟 Key Engineering Principles

### Evidence First

Retrieve evidence before generating an answer.

### Hybrid Retrieval

Combine semantic understanding with exact lexical retrieval.

### Rerank Before Generation

Do not send every retrieved document to the LLM.

### Source Grounding

Generated answers should remain connected to retrieved evidence.

### Reproducibility

Medical evidence processing should be repeatable and evaluable.

### Modular Architecture

Data ingestion, retrieval, reranking, generation, frontend, and evaluation should remain independently maintainable.

---

# 📈 Future Roadmap

- [ ] Advanced query understanding
- [ ] Improved medical entity recognition
- [ ] More oncology guideline integration
- [ ] Advanced evidence scoring
- [ ] Better citation verification
- [ ] Clinical evidence graph
- [ ] Multi-document reasoning
- [ ] Temporal evidence tracking
- [ ] Personalized evidence retrieval
- [ ] Expanded evaluation benchmarks
- [ ] Production-grade observability
- [ ] Authentication and role-based access
- [ ] Scalable cloud deployment
- [ ] Multi-specialty medical expansion

---

# 🤝 Contribution

Contributions, ideas, and research collaboration are welcome.

A typical contribution workflow:

```bash
git checkout -b feature/your-feature

git add .

git commit -m "feat: add your feature"

git push origin feature/your-feature
```

Then open a Pull Request.

---

# 📄 License

Add the project's license information here.

If this repository is intended for research or startup development, clearly define the permitted use of the software, data, and documentation.

---

# ⚠️ Disclaimer

**Indence is a research and engineering project intended to assist with medical evidence retrieval and information discovery.**

It does not provide medical diagnoses or replace professional medical advice.

Always verify important medical information against authoritative clinical guidelines and consult qualified healthcare professionals before making clinical decisions.

---

# ⭐ Vision

> **Make high-quality medical evidence easier to find, understand, verify, and use.**

Indence aims to move beyond traditional medical search and generic AI chat toward an **evidence-first clinical intelligence system** where retrieval, ranking, reasoning, and citations work together.

---

## 🧬 Indence

**Evidence-first clinical intelligence for modern healthcare.**

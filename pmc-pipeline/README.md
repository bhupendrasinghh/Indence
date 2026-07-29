---
title: PMC Oncology Ingestion Pipeline
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: gradio
app_file: app.py
pinned: false
---

# PubMed Central (PMC) Oncology Literature Ingestion Pipeline & Dashboard


An end-to-end asynchronous Python pipeline and interactive web dashboard designed to query, filter, download, parse, and persist Open Access (OA) oncology literature from PubMed Central (PMC).

The system targets specific publication types (Randomized Controlled Trials, Systematic Reviews, Meta-Analyses, Practice Guidelines) in the oncology domain and builds a structured local corpus of full-text papers.

---

## Features

### 🚀 Asynchronous Data Ingestion Pipeline
- **Stage 1: Domain-Specific Search:** Uses NCBI E-utilities (`esearch`) to identify papers within the past 5 years matching precise oncology queries and MeSH terms.
- **Stage 1.5: Species Metadata Filtering:** Resolves PubMed MeSH indices to exclude animal-only studies and focus strictly on human research.
- **Stage 2: Identifier Conversion:** Translates PubMed IDs (PMIDs) to PubMed Central IDs (PMCIDs) and DOIs in bulk batches.
- **Stage 3: Open Access Verification:** Filters PMCIDs against the PMC OAI-PMH service to verify OA licensing and retrieve full-text XML download URLs.
- **Stage 4: Asynchronous Downloader:** Downloads full-text XMLs using `aiohttp` and `asyncio` with:
  - Robust rate-limiting (token bucket algorithm).
  - Exponential backoff retry logic.
  - Checkpoint persistence to safely resume interrupted runs.
- **Deduplication & Extraction:** Parses PMC XML articles (using `lxml`), extracts structured metadata (Title, Authors, Journal, Dates, Abstract, Sections, Figures, Tables, Citations).

### 🗂️ Advanced Ingestion Schema & RAG Preparation
- **Section-Aware Chunking Engine (`pipeline/chunker.py`):** Splits document sections into overlapping child chunks (256-320 tokens) and parent evidence units (600-1200 tokens) respecting sentence and table row boundaries.
- **Qdrant Vector Indexing Adapter (`pipeline/vector_index.py`):** Inserts dense and sparse vectors into Qdrant collections. Operates in mock mode if `qdrant-client` is missing.
- **JSON Schema Validation Contract (`pipeline/manifest_validator.py`):** Validates ingestion payloads against `integrations/ingestion_contract/v1.schema.json` before database indexing.
- ** SQLAlchemy 2.0 PostgreSQL/SQLite Schema (`pipeline/models.py`):** Declares relational entities mapping users, sessions, corpus snapshots, revisions, document sections, chunks, evidence cards, query traces, and user feedback.
- **Data Backfill Migration Utility (`scripts/migrate_to_postgres.py`):** Standard script to read SQLite cache (`papers.db`), parse XMLs, segment them into parent-child schemas, validate contracts, and load them into a PostgreSQL database or local SQLite mirror.

### 📊 Web-Based Management Dashboard
- **Real-Time Pipeline Tracking:** Monitor current execution logs, active stages, throughput, and progress.
- **Footprint Estimator:** Run dry-run estimations to calculate the expected storage footprint (database size, XML/JSON storage requirements) before initiating massive downloads.
- **Corpus Analytics:** Visual statistics on downloaded article types, publication dates, and database volume.
- **Control Interface:** Start, pause, or kill pipeline execution dynamically from the web page.

---

## Project Structure

```
pmc-pipeline/
├── dashboard/                   # Web dashboard codebase.
│   ├── app.py                   # Aiohttp web server & subprocess supervisor.
│   └── static/                  # HTML, CSS, and JS files for the UI
│       ├── index.html
│       ├── style.css
│       └── app.js
├── data/                        # Local data directory (Git ignored)
│   ├── checkpoints/             # Resume state json files
│   ├── json/                    # Parsed structured JSON articles
│   ├── xml/                     # Raw full-text XML articles
│   ├── logs/                    # Execution logs
│   └── metadata/                # SQLite database (papers.db & postgres_mirror.db)
├── integrations/
│   └── ingestion_contract/
│       └── v1.schema.json       # JSON Schema validation contract definition
├── pipeline/                    # Pipeline modular package
│   ├── config.py                # Config parser with .env & env var fallback
│   ├── database.py              # SQLite schema & database handlers
│   ├── dedup.py                 # File & database deduplication logic
│   ├── downloader.py            # XML fetcher & XML parsing engine.
│   ├── id_converter.py          # PMID to PMCID translator
│   ├── oa_filter.py             # Open Access license validation
│   ├── search.py                # PubMed E-utilities searcher
│   ├── species_filter.py        # Species MeSH verification
│   ├── chunker.py               # Parent-child chunking & sentence offset parser
│   ├── manifest_validator.py    # Schema validation runner
│   ├── models.py                # SQLAlchemy relational target tables models
│   ├── vector_index.py          # Qdrant client dense+sparse index adapter
│   └── utils.py                 # Checkpoints, rate limiting, and trackers
├── scripts/
│   ├── migrate_to_postgres.py   # Backfills flat SQLite corpus to target models schema
│   └── inspect_mirror.py        # Script to inspect migrated DB sections & chunks
├── tests/
│   └── test_migration.py        # Schema and chunking engine unit tests
├── .env.example                 # Template for setting up environment variables
├── .gitignore                   # Standard gitignore configurations
├── config.yaml.example          # Template for project-wide configuration parameters
├── requirements.txt             # Python dependency list
├── run_pipeline.py              # Main pipeline execution entry point
└── update_papers.py             # Utility to manually query and update the database
```

---

## Requirements & Setup

### 1. Prerequisites
- Python 3.9 or higher installed.
- Git.

### 2. Installation
Clone the repository:
```bash
git clone https://github.com/Cosmicgod5151/data_ingestion_pipeline.git
cd data_ingestion_pipeline
```

Set up a virtual environment and install the required dependencies:
```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
The application requires a configuration file and optionally reads credentials from environment variables to prevent rate limiting (NCBI limits requests without keys).

1. Copy the example configuration:
   ```bash
   cp config.yaml.example config.yaml
   ```
2. Configure environment variables. You can set these directly in your shell or create a local `.env` file in the root folder:
   ```bash
   # Create a local .env file
   echo NCBI_API_KEY=your_ncbi_api_key_here >> .env
   echo NCBI_EMAIL=your_email_here >> .env
   ```
   *Note: Creating a `.env` file is highly recommended to protect your keys. Both `config.yaml` and `.env` are listed in `.gitignore` and will not be committed.*

---

## Usage

### Running the Ingestion Pipeline
You can trigger the pipeline from the command line inside your activated virtual environment:

```bash
# Run the pipeline from scratch
python run_pipeline.py

# Resume an interrupted run (loads the last stage checkpoint)
python run_pipeline.py --resume

# Run a dry-run (searches and checks counts but doesn't download XML/JSON)
python run_pipeline.py --dry-run

# Limit the maximum number of papers to download
python run_pipeline.py --max-papers 500

# Specify a custom configuration file path
python run_pipeline.py --config my_config.yaml
```

### Launching the Web Dashboard
To view corpus analytics, calculate storage estimates, or run/monitor the pipeline from your browser, launch the dashboard:

```bash
python dashboard/app.py
```
After launching, open your browser and navigate to:
```
http://localhost:8080
```

### 📂 Database Migration & RAG Backfilling
If you have run the pipeline and populated the SQLite cache, you can process raw XMLs, extract chunks, validate JSON schemas, and load them into your relational database:

```bash
# Migrate SQLite data to a target SQLite mirror database (default)
python scripts/migrate_to_postgres.py

# Migrate SQLite data to a PostgreSQL instance
python scripts/migrate_to_postgres.py --db-url "postgresql://user:password@localhost:5432/dbname"

# Verify migrated tables, chunks, and sentence structures
python scripts/inspect_mirror.py
```

### 🧪 Running Unit Tests
You can run automated schema validation and chunker unit tests:
```bash
python -m unittest tests/test_migration.py
```

---

## Security & Best Practices
To maintain clean and secure repository structures, the following guidelines are configured:
- **Zero Credentials Committed:** API keys, emails, and tokens are decoupled from the static yaml files and loaded via `.env` or system environment variables.
- **Corpus Segregation:** Raw downloads (`data/xml/`, `data/json/`), run checkpoints, databases, and logs are saved inside the gitignored `data/` folder.
- **Environment Isolation:** The `venv` directory is excluded from version control.

---

## 🌐 Deployment & Containerization

This project includes configuration setups for local containerized usage and one-click cloud deployment.

### 1. Run with Docker
You can package the entire application inside a Docker container, enabling it to run seamlessly on Windows, macOS, or Linux without manual python setup.

```bash
# Build the Docker image
docker build -t pmc-pipeline .

# Run the container (maps dashboard to port 7860)
# The local database and download files are mounted to a persistent Docker volume 'pmc_data'
docker run -d \
  -p 7860:7860 \
  -v pmc_data:/app/data \
  --name pmc-pipeline \
  pmc-pipeline
```

To run with NCBI API keys passed dynamically:
```bash
docker run -d \
  -p 7860:7860 \
  -v pmc_data:/app/data \
  -e NCBI_API_KEY="your_api_key" \
  -e NCBI_EMAIL="your_email" \
  --name pmc-pipeline \
  pmc-pipeline
```

### 2. Deploy to the Cloud (Render / Railway)
Since the application adheres to standard containerization protocols:
- **Render Deployment:** 
  1. Push your changes to your GitHub repository.
  2. Create a new **Web Service** on [Render](https://render.com).
  3. Connect your repository and select **Docker** as the environment.
  4. Render will automatically read the `render.yaml` template and launch the dashboard web service.
- **Railway / Fly.io:** Directly import the GitHub repository; their engines will auto-detect the `Dockerfile` and deploy the service.

*(Note: Ensure your cloud provider's service supports persistent volumes if you wish to persist the SQLite database and raw files across redeployments).*

import asyncio
import os
import sys
import json
import logging
from pathlib import Path
from xml.etree import ElementTree as ET
import aiohttp
from aiohttp import web

# Set up paths relative to project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import load_config
from pipeline.database import PaperDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard")

# Global reference to running subprocess and its logs
active_process = None
active_task_type = None  # "discover" or "expand"
log_buffer = []

# Load pipeline config
config = load_config(str(PROJECT_ROOT / "config.yaml"))

async def get_storage_stats():
    """Calculate storage sizes and actual averages from existing corpus."""
    db_path = Path(config.paths.database)
    xml_dir = Path(config.paths.xml_dir)
    json_dir = Path(config.paths.json_dir)

    # 1. Total sizes
    db_size = db_path.stat().st_size if db_path.exists() else 0
    
    xml_size = 0
    xml_count = 0
    if xml_dir.exists():
        for f in xml_dir.glob("*.xml"):
            xml_size += f.stat().st_size
            xml_count += 1
            
    json_size = 0
    json_count = 0
    if json_dir.exists():
        for f in json_dir.glob("*.json"):
            json_size += f.stat().st_size
            json_count += 1

    # 2. Query total rows in database to calculate database avg footprint
    total_papers = 0
    async with PaperDatabase(str(db_path)) as db:
        stats = await db.get_stats()
        total_papers = stats.get("total", 0)

    # 3. Calculate measured averages (with fallback baseline if corpus is empty)
    avg_xml = (xml_size / xml_count) if xml_count > 0 else (50 * 1024)
    avg_json = (json_size / json_count) if json_count > 0 else (40 * 1024)
    avg_db = (db_size / total_papers) if total_papers > 0 else (10 * 1024)

    return {
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "xml_size_mb": round(xml_size / (1024 * 1024), 2),
        "json_size_mb": round(json_size / (1024 * 1024), 2),
        "total_size_mb": round((db_size + xml_size + json_size) / (1024 * 1024), 2),
        "measured_avg_xml_kb": round(avg_xml / 1024, 2),
        "measured_avg_json_kb": round(avg_json / 1024, 2),
        "measured_avg_db_kb": round(avg_db / 1024, 2),
        "measured_total_avg_kb": round((avg_xml + avg_json + avg_db) / 1024, 2),
        "xml_count": xml_count,
        "json_count": json_count,
        "total_papers": total_papers
    }

async def read_subprocess_output(stream):
    """Read subprocess stdout line-by-line and append to log buffer."""
    global log_buffer
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded_line = line.decode("utf-8", errors="replace").strip()
        log_buffer.append(decoded_line)
        # Limit buffer to last 1000 lines
        if len(log_buffer) > 1000:
            log_buffer.pop(0)
        logger.info(f"[pipeline] {decoded_line}")

# -- API Routes -------------------------------------------------------------

async def api_stats(request):
    """Retrieve corpus statistics, status breakdowns, and active offsets."""
    db_path = Path(config.paths.database)
    checkpoint_path = Path(config.paths.checkpoints_dir) / "discovery_state.json"

    # 1. Fetch from Database
    status_counts = {}
    category_counts = {}
    last_run_date = "N/A"
    
    async with PaperDatabase(str(db_path)) as db:
        status_counts = await db.get_paper_count_by_status()
        
        # Count by category
        rows = await db._db.execute_fetchall(
            "SELECT evidence_category, COUNT(*) FROM papers GROUP BY evidence_category"
        )
        category_counts = {r[0]: r[1] for r in rows if r[0] is not None}

        # Last run date from history
        row = await db._db.execute_fetchall(
            "SELECT run_date FROM run_history ORDER BY id DESC LIMIT 1"
        )
        if row:
            last_run_date = row[0][0]

    # Ensure all statuses have a default 0
    all_statuses = [
        "DISCOVERED", "QUEUED_FOR_DOWNLOAD", "DOWNLOADING", "DOWNLOADED",
        "PROCESSING", "PROCESSED", "EMBEDDING_PENDING", "EMBEDDED",
        "FAILED_DOWNLOAD", "FAILED_PROCESSING", "FAILED_EMBEDDING", "excluded", "duplicate"
    ]
    for s in all_statuses:
        status_counts.setdefault(s, 0)

    # 2. Fetch Checkpoint Offsets
    offsets = {"rct": 0, "meta_analysis": 0, "systematic_review": 0, "guideline": 0}
    if checkpoint_path.exists():
        try:
            state = json.loads(checkpoint_path.read_text())
            offsets.update(state.get("offsets", {}))
        except Exception as e:
            logger.warning(f"Error reading discovery checkpoint: {e}")

    # 3. Calculate storage metrics
    storage = await get_storage_stats()

    # 4. Get active queries from config
    queries = {k: v.strip() for k, v in config.search.queries.items()}

    return web.json_response({
        "pool": {
            "available_to_download": status_counts.get("DISCOVERED", 0),
            "status_counts": status_counts
        },
        "discovery": {
            "last_run_timestamp": last_run_date,
            "offsets": offsets,
            "counts_in_db": category_counts
        },
        "queries": queries,
        "storage": storage
    })

async def api_estimate(request):
    """Estimate corpus size via PubMed esearch(retmax=0) and calculate dynamic storage sizes."""
    try:
        data = await request.json()
        pub_start = int(data.get("pub_start", 2020))
        pub_end = int(data.get("pub_end", 2025))
        categories = data.get("categories", ["rct", "meta_analysis", "systematic_review", "guideline"])
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON payload: {e}"}, status=400)

    # Date filter formatting
    date_filter = f'("{pub_start}/01/01"[Date - Publication] : "{pub_end}/12/31"[Date - Publication])'
    
    estimates = {}
    total_discoverable = 0

    # Query PubMed for each selected category
    async with aiohttp.ClientSession() as session:
        for cat in categories:
            query_template = config.search.queries.get(cat)
            if not query_template:
                continue

            full_query = f"{query_template.strip()} AND {date_filter}"
            params = {
                "db": "pubmed",
                "term": full_query,
                "retmax": "0",
                "retmode": "xml",
            }
            if config.ncbi.api_key:
                params["api_key"] = config.ncbi.api_key

            try:
                async with session.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params) as resp:
                    resp.raise_for_status()
                    xml_text = await resp.text()
                
                root = ET.fromstring(xml_text)
                count = int(root.findtext("Count", "0"))
                estimates[cat] = count
                total_discoverable += count
            except Exception as e:
                logger.error(f"Error querying PubMed for {cat}: {e}")
                estimates[cat] = 0

    # Calculate actual storage statistics to get current paper footprint
    storage_stats = await get_storage_stats()
    avg_paper_kb = storage_stats["measured_total_avg_kb"]

    # Calculate dynamic storage predictions (multi-tier)
    expected_kb = total_discoverable * avg_paper_kb
    optimistic_kb = expected_kb * 0.50
    conservative_kb = expected_kb * 1.80

    return web.json_response({
        "estimates": estimates,
        "total_discoverable": total_discoverable,
        "measured_avg_paper_kb": avg_paper_kb,
        "storage_predictions": {
            "optimistic_mb": round(optimistic_kb / 1024, 2),
            "expected_mb": round(expected_kb / 1024, 2),
            "conservative_mb": round(conservative_kb / 1024, 2)
        }
    })

async def api_discover(request):
    """Launch a discovery run subprocess."""
    global active_process, active_task_type, log_buffer
    if active_process is not None and active_process.returncode is None:
        return web.json_response({"error": "A pipeline run is already in progress"}, status=400)

    try:
        data = await request.json()
        limit = data.get("limit")
    except Exception:
        limit = None

    args = ["run_pipeline.py", "--mode", "discover"]
    if limit is not None:
        args.extend(["--limit", str(limit)])

    log_buffer = [f"Starting discovery run: python {' '.join(args)}"]
    active_task_type = "discover"

    try:
        active_process = await asyncio.create_subprocess_exec(
            sys.executable, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT)
        )
        asyncio.create_task(read_subprocess_output(active_process.stdout))
        return web.json_response({"status": "started", "task": "discover"})
    except Exception as e:
        logger.error(f"Failed to start discovery process: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def api_expand(request):
    """Launch an expansion/download run subprocess."""
    global active_process, active_task_type, log_buffer
    if active_process is not None and active_process.returncode is None:
        return web.json_response({"error": "A pipeline run is already in progress"}, status=400)

    try:
        data = await request.json()
        count = data.get("count")
        sort_order = data.get("sort", "newest")
        pub_start = data.get("pub_start")
        pub_end = data.get("pub_end")
    except Exception:
        count = None
        sort_order = "newest"
        pub_start = None
        pub_end = None

    args = ["run_pipeline.py", "--mode", "expand", "--sort", sort_order]
    if count is not None:
        args.extend(["--count", str(count)])
    if pub_start is not None:
        args.extend(["--pub-start", str(pub_start)])
    if pub_end is not None:
        args.extend(["--pub-end", str(pub_end)])

    log_buffer = [f"Starting expansion run: python {' '.join(args)}"]
    active_task_type = "expand"

    try:
        active_process = await asyncio.create_subprocess_exec(
            sys.executable, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT)
        )
        asyncio.create_task(read_subprocess_output(active_process.stdout))
        return web.json_response({"status": "started", "task": "expand"})
    except Exception as e:
        logger.error(f"Failed to start expansion process: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def api_status(request):
    """Get active runner status and logs."""
    global active_process, active_task_type, log_buffer
    
    is_running = active_process is not None and active_process.returncode is None
    exit_code = active_process.returncode if active_process else None
    
    return web.json_response({
        "running": is_running,
        "task_type": active_task_type if is_running else None,
        "exit_code": exit_code,
        "logs": log_buffer[-200:]  # last 200 lines of logs
    })

async def api_kill(request):
    """Terminate the active runner process."""
    global active_process
    if active_process is None or active_process.returncode is not None:
        return web.json_response({"status": "inactive"})

    try:
        active_process.terminate()
        # wait a bit for termination
        await asyncio.sleep(0.5)
        if active_process.returncode is None:
            active_process.kill()
        return web.json_response({"status": "killed"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

# -- Setup static assets and server lifecycle -------------------------------

app = web.Application()
app.router.add_get("/api/stats", api_stats)
app.router.add_post("/api/estimate", api_estimate)
app.router.add_post("/api/discover", api_discover)
app.router.add_post("/api/expand", api_expand)
app.router.add_get("/api/status", api_status)
app.router.add_post("/api/kill", api_kill)

# Static routes
STATIC_DIR = PROJECT_ROOT / "dashboard" / "static"
app.router.add_static("/static/", path=STATIC_DIR, name="static")

# Default index.html redirect
async def index_handler(request):
    return web.FileResponse(STATIC_DIR / "index.html")
app.router.add_get("/", index_handler)

def main():
    import argparse
    port_env = os.environ.get("PORT")
    default_port = int(port_env) if port_env else 8080

    parser = argparse.ArgumentParser(description="Ingestion Pipeline Dashboard Server")
    parser.add_argument("--port", type=int, default=default_port, help=f"Dashboard port (default: {default_port})")
    args = parser.parse_args()

    # Create directories if missing
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Starting dashboard server at http://0.0.0.0:{args.port}")
    web.run_app(app, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    main()

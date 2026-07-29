import sys
from types import ModuleType

try:
    import spaces
except ImportError:
    mock_spaces = ModuleType("spaces")
    def dummy_gpu(func):
        return func
    mock_spaces.GPU = dummy_gpu
    sys.modules["spaces"] = mock_spaces
    import spaces

import asyncio
import os
import logging
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
import aiohttp
import gradio as gr

@spaces.GPU
def zero_gpu_dummy_trigger():
    """Dummy function to satisfy Hugging Face ZeroGPU startup scanner."""
    pass

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hf_dashboard")

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import load_config
from pipeline.database import PaperDatabase

# Global configuration
config_path = PROJECT_ROOT / "config.yaml"
# Ensure config file exists for Hugging Face run
if not config_path.exists():
    import shutil
    shutil.copy(PROJECT_ROOT / "config.yaml.example", config_path)

config = load_config(str(config_path))

# Global process tracking
active_process = None
active_task_type = None
last_logged_lines = 0

# Log file for progress streaming
LOG_FILE_PATH = PROJECT_ROOT / "data" / "logs" / "pipeline_run.log"

def get_db_stats_sync():
    """Fetch database and file stats synchronously for Gradio interface."""
    db_path = Path(config.paths.database)
    xml_dir = Path(config.paths.xml_dir)
    json_dir = Path(config.paths.json_dir)

    # File counts
    xml_count = len(list(xml_dir.glob("*.xml"))) if xml_dir.exists() else 0
    json_count = len(list(json_dir.glob("*.json"))) if json_dir.exists() else 0

    # DB Stats
    total = 0
    failed = 0
    excluded = 0
    
    if db_path.exists():
        try:
            # Run simple query to fetch stats
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            
            # Check table existence
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='papers'")
            if cursor.fetchone():
                cursor.execute("SELECT status, COUNT(*) FROM papers GROUP BY status")
                for status, count in cursor.fetchall():
                    if status == "EMBEDDED":
                        total = count
                    elif status == "FAILED_DOWNLOAD":
                        failed = count
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='excluded_papers'")
            if cursor.fetchone():
                cursor.execute("SELECT COUNT(*) FROM excluded_papers")
                row = cursor.fetchone()
                if row:
                    excluded = row[0]
                    
            conn.close()
        except Exception as e:
            logger.error(f"Error reading sqlite stats: {e}")

    return {
        "Total Papers in DB": total,
        "Failed Downloads": failed,
        "Excluded Papers": excluded,
        "Raw XML Files": xml_count,
        "Parsed JSON Files": json_count,
    }

async def async_estimate(pub_start, pub_end, categories):
    """Estimate total discoverable papers on PubMed Central."""
    date_filter = f'("{pub_start}/01/01"[Date - Publication] : "{pub_end}/12/31"[Date - Publication])'
    estimates = {}
    total_discoverable = 0

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

    # Calculate dynamic sizing estimations
    # Standard averages: XML ~50KB, JSON ~40KB, DB footprint ~2KB
    avg_paper_kb = 92.0 # ~90KB total footprint
    expected_mb = (total_discoverable * avg_paper_kb) / 1024
    
    details_str = "\n".join([f"- **{cat.upper()}**: {count:,} papers" for cat, count in estimates.items()])
    return (
        f"### Ingestion Estimate Results\n\n"
        f"**Total Discoverable Papers:** {total_discoverable:,}\n\n"
        f"{details_str}\n\n"
        f"**Predicted Storage Footprint:**\n"
        f"- Optimistic (50%): **{expected_mb * 0.50:.2f} MB**\n"
        f"- Expected (100%): **{expected_mb:.2f} MB**\n"
        f"- Conservative (180%): **{expected_mb * 1.80:.2f} MB**"
    )

def start_pipeline_run(mode, limit):
    """Launch the ingestion pipeline in a background process."""
    global active_process, active_task_type, last_logged_lines
    logger.info(f"start_pipeline_run called with mode={mode}, limit={limit}")
    if active_process is not None and active_process.poll() is None:
        logger.warning("Pipeline process is already running.")
        return "A pipeline process is already running."

    # Reset log tracking
    last_logged_lines = 0

    # Make sure logs directory exists
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    args = [sys.executable, "run_pipeline.py", "--mode", mode]
    if limit and int(limit) > 0:
        args.extend(["--limit", str(limit)])

    logger.info(f"Subprocess args: {args}")

    # Clear previous logs
    try:
        with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(f"--- Launching pipeline in mode: {mode.upper()} ---\n")
    except Exception as e:
        logger.error(f"Failed to write initial log header: {e}")
        return f"Failed to open log file: {str(e)}"

    # Start subprocess
    try:
        log_file = open(LOG_FILE_PATH, "a", encoding="utf-8")
        active_process = subprocess.Popen(
            args,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT)
        )
        active_task_type = mode
        logger.info("Subprocess started successfully.")
        return f"Successfully started pipeline run in '{mode}' mode."
    except Exception as e:
        logger.error(f"Failed to start pipeline subprocess: {e}", exc_info=True)
        return f"Failed to start pipeline: {str(e)}"

def stop_pipeline_run():
    """Kill the active pipeline process."""
    global active_process
    if active_process is None or active_process.poll() is not None:
        return "No pipeline process is currently running."
    
    try:
        active_process.terminate()
        # Wait a moment for it to terminate
        for _ in range(10):
            if active_process.poll() is not None:
                break
            import time
            time.sleep(0.1)
        
        if active_process.poll() is None:
            active_process.kill()
            
        return "Pipeline process has been terminated."
    except Exception as e:
        return f"Error stopping process: {str(e)}"

def read_logs():
    """Read the latest log lines for the UI status."""
    global active_process, last_logged_lines
    status_msg = "🟢 Idle"
    if active_process is not None and active_process.poll() is None:
        status_msg = f"⚡ Running ({active_task_type.upper()} mode)"
    elif active_process is not None:
        status_msg = f"🔴 Stopped (Exit Code: {active_process.returncode})"

    log_content = ""
    if LOG_FILE_PATH.exists():
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_content = "".join(lines[-40:])
                # Stream new lines to container console logs for debugging
                if len(lines) > last_logged_lines:
                    for line in lines[last_logged_lines:]:
                        logger.info(f"[PIPE_LOG] {line.strip()}")
                    last_logged_lines = len(lines)
        except Exception as e:
            log_content = f"Error reading logs: {e}"
    else:
        log_content = "No logs available. Click 'Start Pipeline' to begin."

    return status_msg, log_content

# --- Gradio UI Design ---
with gr.Blocks(theme=gr.themes.Soft(), title="PMC Oncology Ingestion Dashboard") as demo:
    gr.Markdown("# 📊 PubMed Central Oncology Ingestion Dashboard")
    gr.Markdown("An automated system to catalog, verify, download, and parse Open Access oncology literature.")
    
    with gr.Row():
        # Left Column: Statistics & Status
        with gr.Column(scale=1):
            gr.Markdown("### 📈 Corpus Statistics")
            stats_box = gr.JSON(value=get_db_stats_sync, label="Current Storage Volume")
            btn_refresh = gr.Button("🔄 Refresh Stats", variant="secondary")
            
            gr.Markdown("### ⚙️ Pipeline Control")
            status_display = gr.Label(value="🟢 Idle", label="Pipeline Status")
            
            mode_input = gr.Radio(choices=["discover", "expand", "update"], value="discover", label="Execution Mode")
            limit_input = gr.Number(value=0, label="Limit (0 for no limit)", precision=0)
            
            with gr.Row():
                btn_start = gr.Button("▶️ Start Pipeline", variant="primary")
                btn_stop = gr.Button("⏹️ Stop Pipeline", variant="stop")

        # Right Column: Estimation & Logs
        with gr.Column(scale=2):
            with gr.Tab("📋 Real-Time Execution Logs"):
                log_display = gr.Code(value="No logs yet.", language="shell", label="Pipeline Output Logs", interactive=False)
                # Auto-refresh log timer every 2 seconds
                log_timer = gr.Timer(2.0)
                log_timer.tick(fn=read_logs, outputs=[status_display, log_display])

            with gr.Tab("🧮 Corpus Footprint Estimator"):
                gr.Markdown("Predict the space requirements for new query parameters before starting imports.")
                with gr.Row():
                    year_start = gr.Slider(minimum=2015, maximum=2026, value=2020, step=1, label="Publication Start Year")
                    year_end = gr.Slider(minimum=2015, maximum=2026, value=2025, step=1, label="Publication End Year")
                
                categories_input = gr.CheckboxGroup(
                    choices=["rct", "meta_analysis", "systematic_review", "guideline"],
                    value=["rct", "meta_analysis", "systematic_review", "guideline"],
                    label="Search Categories"
                )
                
                btn_estimate = gr.Button("🔍 Calculate Estimate", variant="primary")
                estimate_output = gr.Markdown("Click 'Calculate Estimate' to query PubMed Central.")

    # Controller wiring
    btn_refresh.click(fn=get_db_stats_sync, outputs=stats_box)
    
    btn_estimate.click(
        fn=lambda y_start, y_end, cats: asyncio.run(async_estimate(y_start, y_end, cats)),
        inputs=[year_start, year_end, categories_input],
        outputs=estimate_output
    )
    
    btn_start.click(
        fn=start_pipeline_run,
        inputs=[mode_input, limit_input],
        outputs=log_display
    ).then(
        fn=read_logs,
        outputs=[status_display, log_display]
    )

    btn_stop.click(
        fn=stop_pipeline_run,
        outputs=log_display
    ).then(
        fn=read_logs,
        outputs=[status_display, log_display]
    )

if __name__ == "__main__":
    demo.launch()

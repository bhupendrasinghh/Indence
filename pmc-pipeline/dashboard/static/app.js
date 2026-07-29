// Global active status tracking
let isPolling = true;

// Tab Switcher
function switchTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
    });

    const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(
        btn => btn.getAttribute('onclick').includes(tabId)
    );
    if (activeBtn) activeBtn.classList.add('active');

    const pane = document.getElementById(tabId);
    if (pane) pane.classList.add('active');
}

// Accordion Toggler
function toggleAccordion(contentId) {
    const content = document.getElementById(contentId);
    const item = content.parentElement;
    
    if (item.classList.contains('open')) {
        item.classList.remove('open');
        content.style.maxHeight = null;
    } else {
        item.classList.add('open');
        content.style.maxHeight = content.scrollHeight + "px";
    }
}

// Format numbers with thousands separators
function formatNum(num) {
    return Number(num).toLocaleString();
}

// Fetch stats and update UI elements
async function fetchStats() {
    try {
        const resp = await fetch('/api/stats');
        if (!resp.ok) throw new Error('API server returned error');
        
        const data = await resp.json();
        
        // 1. Update Sidebar Overall Footprint
        document.getElementById('stat-total-papers').innerText = formatNum(data.storage.total_papers);
        document.getElementById('stat-avail-expand').innerText = formatNum(data.pool.available_to_download);
        
        document.getElementById('stat-db-size').innerText = `${data.storage.db_size_mb} MB`;
        document.getElementById('stat-xml-size').innerText = `${data.storage.xml_size_mb} MB`;
        document.getElementById('stat-json-size').innerText = `${data.storage.json_size_mb} MB`;
        document.getElementById('stat-total-size').innerText = `${data.storage.total_size_mb} MB`;

        // 2. Update Ingestion Funnel Columns
        document.getElementById('funnel-discovered').innerText = formatNum(data.pool.status_counts.DISCOVERED);
        
        const downloaded = data.pool.status_counts.DOWNLOADED + data.pool.status_counts.DOWNLOADING;
        document.getElementById('funnel-downloaded').innerText = formatNum(downloaded);
        
        const processed = data.pool.status_counts.PROCESSED + data.pool.status_counts.PROCESSING;
        document.getElementById('funnel-processed').innerText = formatNum(processed);
        
        document.getElementById('funnel-embedded').innerText = formatNum(data.pool.status_counts.EMBEDDED);
        
        const failed = data.pool.status_counts.FAILED_DOWNLOAD + 
                       data.pool.status_counts.FAILED_PROCESSING + 
                       data.pool.status_counts.FAILED_EMBEDDING;
        document.getElementById('funnel-failed').innerText = formatNum(failed);

        // 3. Update Discovery Matrix table
        document.getElementById('last-run-timestamp').innerText = data.discovery.last_run_timestamp;
        
        document.getElementById('matrix-offset-rct').innerText = formatNum(data.discovery.offsets.rct);
        document.getElementById('matrix-offset-ma').innerText = formatNum(data.discovery.offsets.meta_analysis);
        document.getElementById('matrix-offset-sr').innerText = formatNum(data.discovery.offsets.systematic_review);
        document.getElementById('matrix-offset-gl').innerText = formatNum(data.discovery.offsets.guideline);

        document.getElementById('matrix-db-rct').innerText = formatNum(data.discovery.counts_in_db.rct || 0);
        document.getElementById('matrix-db-ma').innerText = formatNum(data.discovery.counts_in_db.meta_analysis || 0);
        document.getElementById('matrix-db-sr').innerText = formatNum(data.discovery.counts_in_db.systematic_review || 0);
        document.getElementById('matrix-db-gl').innerText = formatNum(data.discovery.counts_in_db.guideline || 0);

        // 4. Update Ingestion Query Accordion code sections
        document.querySelector('#acc-rct pre').innerText = data.queries.rct;
        document.querySelector('#acc-ma pre').innerText = data.queries.meta_analysis;
        document.querySelector('#acc-sr pre').innerText = data.queries.systematic_review;
        document.querySelector('#acc-gl pre').innerText = data.queries.guideline;

    } catch (e) {
        console.error('Error fetching statistics:', e);
    }
}

// Fetch Active process runner logs and status
async function fetchRunnerStatus() {
    try {
        const resp = await fetch('/api/status');
        if (!resp.ok) throw new Error('API server returned error');
        
        const data = await resp.json();
        
        const badge = document.getElementById('global-status-badge');
        const text = document.getElementById('global-status-text');
        const killBtn = document.getElementById('kill-btn');

        if (data.running) {
            badge.classList.add('active');
            text.innerText = `Running (${data.task_type.toUpperCase()})`;
            killBtn.classList.remove('hidden');
        } else {
            badge.classList.remove('active');
            text.innerText = 'Idle';
            killBtn.classList.add('hidden');
        }

        // Render logs
        const terminal = document.getElementById('console-logs');
        if (data.logs && data.logs.length > 0) {
            terminal.innerHTML = data.logs.map(line => {
                let cl = 'system';
                if (line.includes('ERROR') || line.includes('Failed')) {
                    cl = 'error';
                } else if (line.includes('INFO')) {
                    cl = 'info';
                }
                return `<div class="log-line ${cl}">${line}</div>`;
            }).join('');
            
            // Auto scroll to bottom
            terminal.scrollTop = terminal.scrollHeight;
        }

    } catch (e) {
        console.error('Error fetching process runner status:', e);
    }
}

// Submit Live Ingestion task triggers
async function triggerDiscovery(event) {
    event.preventDefault();
    const limitVal = document.getElementById('discover-limit').value;
    const body = {};
    if (limitVal) body.limit = parseInt(limitVal);

    try {
        const resp = await fetch('/api/discover', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        const res = await resp.json();
        if (!resp.ok) {
            alert(`Discovery failed to start: ${res.error}`);
        } else {
            appendSystemLog("Discovery pipeline process started.");
        }
    } catch (e) {
        alert(`Error: ${e}`);
    }
}

async function triggerExpansion(event) {
    event.preventDefault();
    const countVal = document.getElementById('expand-count').value;
    const sortVal = document.getElementById('expand-sort').value;
    const startVal = document.getElementById('expand-start').value;
    const endVal = document.getElementById('expand-end').value;

    const body = {
        count: parseInt(countVal),
        sort: sortVal
    };
    if (startVal) body.pub_start = parseInt(startVal);
    if (endVal) body.pub_end = parseInt(endVal);

    try {
        const resp = await fetch('/api/expand', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        const res = await resp.json();
        if (!resp.ok) {
            alert(`Expansion failed to start: ${res.error}`);
        } else {
            appendSystemLog("Expansion/download pipeline process started.");
        }
    } catch (e) {
        alert(`Error: ${e}`);
    }
}

// Estimates calculator
async function getCorpusEstimates(event) {
    event.preventDefault();
    const start = document.getElementById('est-start').value;
    const end = document.getElementById('est-end').value;
    const btn = document.getElementById('est-btn');
    
    const checkboxes = document.querySelectorAll('input[name="est-cat"]:checked');
    const categories = Array.from(checkboxes).map(cb => cb.value);
    
    if (categories.length === 0) {
        alert("Please select at least one evidence type.");
        return;
    }

    btn.disabled = true;
    btn.innerText = "Calculating...";

    try {
        const resp = await fetch('/api/estimate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                pub_start: parseInt(start),
                pub_end: parseInt(end),
                categories: categories
            })
        });
        
        const data = await resp.json();
        btn.disabled = false;
        btn.innerText = "Calculate Estimates";

        if (!resp.ok) {
            alert(`Estimation failed: ${data.error}`);
            return;
        }

        // Show panel
        const results = document.getElementById('est-results');
        results.classList.remove('hidden');

        // Populate counts
        document.getElementById('est-total-papers-val').innerText = formatNum(data.total_discoverable);
        document.getElementById('est-avg-kb').innerText = data.measured_avg_paper_kb.toFixed(2);

        // Format and render MB/GB thresholds
        document.getElementById('est-storage-opt').innerText = formatStorage(data.storage_predictions.optimistic_mb);
        document.getElementById('est-storage-exp').innerText = formatStorage(data.storage_predictions.expected_mb);
        document.getElementById('est-storage-cons').innerText = formatStorage(data.storage_predictions.conservative_mb);

    } catch (e) {
        btn.disabled = false;
        btn.innerText = "Calculate Estimates";
        alert(`Error calculating estimates: ${e}`);
    }
}

// Convert MB to GB if needed for readability
function formatStorage(mb) {
    if (mb >= 1024) {
        return `${(mb / 1024).toFixed(2)} GB`;
    }
    return `${mb.toFixed(2)} MB`;
}

// Process Killer trigger
async function killProcess() {
    if (!confirm("Are you sure you want to terminate the active pipeline run?")) return;
    try {
        const resp = await fetch('/api/kill', {method: 'POST'});
        const res = await resp.json();
        if (resp.ok) {
            appendSystemLog("Process termination signal sent.");
        } else {
            alert(`Error: ${res.error}`);
        }
    } catch (e) {
        alert(`Error: ${e}`);
    }
}

function appendSystemLog(msg) {
    const terminal = document.getElementById('console-logs');
    terminal.innerHTML += `<div class="log-line system">[System] ${msg}</div>`;
    terminal.scrollTop = terminal.scrollHeight;
}

// Initialise polling
function init() {
    fetchStats();
    fetchRunnerStatus();
    
    // Polling schedules
    setInterval(fetchStats, 3000);
    setInterval(fetchRunnerStatus, 1500);
}

document.addEventListener('DOMContentLoaded', init);

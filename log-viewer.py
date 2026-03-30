#!/usr/bin/env python3
"""Temporary log viewer - polls all agent-learn pods and streams to browser."""
import subprocess
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque

NAMESPACE = "agent-learn"
MAX_LINES = 500
logs = {}  # pod_name -> deque of lines
lock = threading.Lock()


SKIP_PATTERNS = [
    "/api/health",
    "GET /api/health",
]


def tail_pod(pod):
    """Stream logs from a pod into the shared dict."""
    proc = subprocess.Popen(
        ["kubectl", "logs", "-f", "--tail=50", pod, "-n", NAMESPACE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        if any(p in line for p in SKIP_PATTERNS):
            continue
        with lock:
            if pod not in logs:
                logs[pod] = deque(maxlen=MAX_LINES)
            logs[pod].append(line)
    proc.wait()


def poll_pods():
    """Discover pods and start tailers for new ones."""
    seen = set()
    while True:
        try:
            out = subprocess.check_output(
                ["kubectl", "get", "pods", "-n", NAMESPACE, "-o", "jsonpath={.items[*].metadata.name}"],
                text=True
            )
            pods = out.strip().split()
            for pod in pods:
                if pod not in seen:
                    seen.add(pod)
                    t = threading.Thread(target=tail_pod, args=(pod,), daemon=True)
                    t.start()
        except Exception:
            pass
        time.sleep(10)


HTML = r"""<!DOCTYPE html>
<html>
<head>
<title>agent-learn logs</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d1117; color: #c9d1d9; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
  .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 20px; position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 14px; font-weight: 600; color: #58a6ff; }
  .header .status { font-size: 11px; color: #8b949e; }
  .header .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #3fb950; margin-right: 4px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
  .controls { margin-left: auto; display: flex; gap: 8px; }
  .controls button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 11px; }
  .controls button:hover { background: #30363d; }
  .controls button.active { background: #1f6feb; border-color: #1f6feb; }
  .pods { display: flex; flex-direction: column; }
  .pod { border-bottom: 1px solid #21262d; }
  .pod-header { background: #161b22; padding: 8px 20px; cursor: pointer; display: flex; align-items: center; gap: 8px; position: sticky; top: 45px; z-index: 5; }
  .pod-header:hover { background: #1c2128; }
  .pod-name { font-weight: 600; color: #79c0ff; font-size: 12px; }
  .pod-count { color: #8b949e; font-size: 10px; background: #21262d; padding: 1px 6px; border-radius: 10px; }
  .arrow { color: #8b949e; font-size: 10px; transition: transform 0.2s; }
  .arrow.open { transform: rotate(90deg); }
  .log-lines { padding: 0; max-height: 400px; overflow-y: auto; display: none; }
  .log-lines.open { display: block; }
  .log-line { padding: 2px 20px; white-space: pre-wrap; word-break: break-all; border-bottom: 1px solid #0d1117; }
  .log-line:hover { background: #161b22; }
  .log-line.error { color: #f85149; }
  .log-line.warn { color: #d29922; }
  .log-line.info { color: #c9d1d9; }
  .filter { background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-family: inherit; width: 200px; }
</style>
</head>
<body>
<div class="header">
  <h1>agent-learn cluster logs</h1>
  <span class="status"><span class="dot"></span>polling every 2s</span>
  <div class="controls">
    <input class="filter" id="filter" placeholder="filter logs..." oninput="applyFilter()">
    <button onclick="toggleAll(true)">Expand All</button>
    <button onclick="toggleAll(false)">Collapse All</button>
    <button id="autoBtn" class="active" onclick="toggleAuto()">Auto-scroll</button>
  </div>
</div>
<div class="pods" id="pods"></div>
<script>
let autoScroll = true;
let filterText = '';
let openPods = {};

function applyFilter() {
  filterText = document.getElementById('filter').value.toLowerCase();
  render(window._lastData || {});
}

function toggleAll(open) {
  document.querySelectorAll('.pod').forEach(function(p) {
    const name = p.dataset.pod;
    openPods[name] = open;
    p.querySelector('.log-lines').classList.toggle('open', open);
    p.querySelector('.arrow').classList.toggle('open', open);
  });
}

function toggleAuto() {
  autoScroll = !autoScroll;
  document.getElementById('autoBtn').classList.toggle('active', autoScroll);
}

function classify(line) {
  const l = line.toLowerCase();
  if (l.includes('error') || l.includes('traceback') || l.includes('exception') || l.includes('failed')) return 'error';
  if (l.includes('warn')) return 'warn';
  return 'info';
}

function render(data) {
  window._lastData = data;
  const container = document.getElementById('pods');
  const pods = Object.keys(data).sort();

  pods.forEach(function(pod) {
    let el = document.querySelector('[data-pod="' + pod + '"]');
    if (!el) {
      el = document.createElement('div');
      el.className = 'pod';
      el.dataset.pod = pod;
      if (!(pod in openPods)) openPods[pod] = true;

      const header = document.createElement('div');
      header.className = 'pod-header';
      header.addEventListener('click', function() { toggle(pod); });

      const arrow = document.createElement('span');
      arrow.className = 'arrow' + (openPods[pod] ? ' open' : '');
      arrow.textContent = '\u25B6';
      header.appendChild(arrow);

      const name = document.createElement('span');
      name.className = 'pod-name';
      name.textContent = pod;
      header.appendChild(name);

      const count = document.createElement('span');
      count.className = 'pod-count';
      count.textContent = '0';
      header.appendChild(count);

      el.appendChild(header);

      const logLines = document.createElement('div');
      logLines.className = 'log-lines' + (openPods[pod] ? ' open' : '');
      el.appendChild(logLines);

      container.appendChild(el);
    }

    let lines = data[pod] || [];
    if (filterText) {
      lines = lines.filter(function(l) { return l.toLowerCase().includes(filterText); });
    }

    el.querySelector('.pod-count').textContent = lines.length + ' lines';
    const logEl = el.querySelector('.log-lines');
    const wasAtBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 50;

    // Clear and rebuild with safe DOM methods
    logEl.textContent = '';
    lines.forEach(function(l) {
      const div = document.createElement('div');
      div.className = 'log-line ' + classify(l);
      div.textContent = l;
      logEl.appendChild(div);
    });

    if (autoScroll && wasAtBottom) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  });
}

function toggle(pod) {
  openPods[pod] = !openPods[pod];
  const el = document.querySelector('[data-pod="' + pod + '"]');
  el.querySelector('.log-lines').classList.toggle('open', openPods[pod]);
  el.querySelector('.arrow').classList.toggle('open', openPods[pod]);
}

async function poll() {
  try {
    const res = await fetch('/logs');
    const data = await res.json();
    render(data);
  } catch(e) {}
  setTimeout(poll, 2000);
}
poll();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/logs":
            with lock:
                data = {k: list(v) for k, v in logs.items()}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())

    def log_message(self, *args):
        pass  # silence request logs


if __name__ == "__main__":
    threading.Thread(target=poll_pods, daemon=True).start()
    print("Log viewer running at http://localhost:9999")
    HTTPServer(("127.0.0.1", 9999), Handler).serve_forever()

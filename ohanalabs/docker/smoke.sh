#!/usr/bin/env bash
# Smoke test of both OhanaLabs graphify images (docker compose --profile smoke). Runs INSIDE the `graphify` CLI image:
#   1. builds a tiny fixture project under /workspace/smoke-fixture and extracts it (`graphify extract --code-only`),
#      exactly like vionix-engram's worker (internal/graphify/run.go);
#   2. calls the `graphify-mcp` service the way vionix-engram's mcpproxy does — a bare JSON-RPC `tools/call` (no
#      initialize), Accept "application/json, text/event-stream", arguments {project_path, question} — for
#      graph_stats and query_graph, and fails unless both answer with non-error text.
set -euo pipefail
MCP_URL="${MCP_URL:-http://graphify-mcp:8080/mcp}"
P=/workspace/smoke-fixture
mkdir -p "$P"
cat > "$P/app.py" <<'PY'
class Store:
    def save(self, item):
        return validate(item)

def validate(item):
    return bool(item)
PY

echo "==> graphify $(graphify --version 2>/dev/null || true): extract $P --code-only"
graphify extract "$P" --code-only >/dev/null
test -s "$P/graphify-out/graph.json" || { echo "FAIL: no graph.json produced"; exit 1; }
echo "ok   graph.json: $(python -c "import json;g=json.load(open('$P/graphify-out/graph.json'));print(len(g['nodes']),'nodes',len(g.get('links',g.get('edges',[]))),'edges')")"

call() { # call <tool> <json-args>
  python - "$MCP_URL" "$1" "$2" <<'PY'
import json, sys, urllib.request
url, tool, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).encode()
req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
r = json.load(urllib.request.urlopen(req, timeout=60))
if r.get("error"):
    print("RPC ERROR", r["error"]); sys.exit(1)
res = r["result"]; text = "".join(p.get("text", "") for p in res.get("content", []) if p.get("type") == "text")
if res.get("isError") or not text.strip():
    print("TOOL ERROR", text[:300]); sys.exit(1)
print(text[:200].replace("\n", " "))
PY
}
# wait for the port (the server has no health route; GET /mcp answers 4xx by design)
python - "$MCP_URL" <<'PY'
import socket, sys, time, urllib.parse
u = urllib.parse.urlparse(sys.argv[1])
for _ in range(60):
    try:
        socket.create_connection((u.hostname, u.port or 80), timeout=2).close(); sys.exit(0)
    except OSError:
        time.sleep(1)
print("FAIL: MCP port never opened"); sys.exit(1)
PY
echo "==> MCP graph_stats (project_path=$P)"; call graph_stats "{\"project_path\": \"$P\"}"
echo "==> MCP query_graph (project_path=$P)"; call query_graph "{\"project_path\": \"$P\", \"question\": \"what does Store.save call?\"}"
echo "==> smoke OK"

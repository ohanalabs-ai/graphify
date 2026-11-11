# `ohanalabs-traversal` — the walk behind an answer, as data (for a live traversal animation)

**Requirement (owner, 2026-11-01):** answers to code questions should show a LIVE graph-traversal animation. Upstream
`query_graph` returns TEXT only — and its MCP interface must not change (Engram depends on it).

**Design:** an OhanaLabs-only Python package (outside `graphify/`) that runs the **same** seed selection and the
**same** BFS/DFS `query_graph` runs, by calling upstream's module-level functions in `graphify/serve.py`
(`_query_name_refusal`, `_query_terms`, `_score_query`, `_pick_seeds`, `_resolve_context_filters`,
`_filter_graph_by_context`, `_traversal_view`, `_bfs`/`_dfs`, `_render_query`) — it never re-implements scoring — and
returns:

```json
{"status": "ok|refused|no_match", "seeds": [{"id","label"}],
 "nodes": [{"step","id","label","depth","parent","community","community_name","file_type","source_file","source_location","repo"}],
 "edges": [{"from","to","source","target","relation","confidence","discovery": true|false,"step"}],
 "node_count": 657, "text": "<the exact query_graph text for these seeds>"}
```

- **BFS** (the `query_graph` default): exact — upstream records `(parent, child)` only when a child joins the next
  frontier, in order, so replaying `edges_seen` gives the visit order, depth and parent; the remaining `edges_seen` are
  the induced edges (`discovery: false`).
- **DFS**: upstream records an edge when it *pushes* a neighbour (some pushes are dropped at pop time), so the visit
  order/stack depth is replayed with upstream's rule set and **verified against upstream's `visited` on every call** —
  on a mismatch it falls back to push order with `depth: null` rather than show a walk the answer did not take.

**Exposure (opt-in, does not touch MCP):** `ohanalabs-traversal` — a small Starlette service in the `graphify-mcp`
image (`--entrypoint ohanalabs-traversal`, default `:8081`), reading the same cached graphs
(`<project_path>/graphify-out/graph.json`) only under `--root` (`TRAVERSAL_ROOT`), optional `X-API-Key`
(`OHANALABS_TRAVERSAL_API_KEY`):
- `POST /v1/trace` `{project_path, question, mode?, depth? (1..6), demote_docs? (default true)}` → the JSON above;
- `GET /v1/trace/stream?project_path=&question=&delay_ms=` → SSE `start` · `seed` · `visit` (node + its discovery
  edge, in order) · `edge` (induced) · `done` (count + the answer text) — drive the animation straight from it.
In Kubernetes it would be a second container (or a second Deployment) next to `engram-graphify-mcp` — not wired yet.

## Ranking parity (upstream >= 0.9.101)

Upstream now ranks source above tests/docs/config (soft weight `_QUERY_DEMOTE_WEIGHT`, via `_query_path_weight`) and
renders the text in that score order (`_subgraph_to_text(scores=)`). The wrapper passes the same `path_weight` and
`scores`, so by default it traces the walk `query_graph` performs (default `demote_docs=True`). `demote_docs=False`
(HTTP `demote_docs`) mirrors the MCP `demote_docs:false` / CLI `--no-doc-demotion` opt-out for docs-first corpora; both
paths are parity-tested. We deliberately did NOT opt out globally: Engram's queries are about code, and the
animation must match what the MCP answers.

## Brittleness across upstream upgrades — what is pinned and tested

- It depends on **private** upstream functions, so it is pinned: `SUPPORTED_UPSTREAM` in `core.py` (today `0.9.100` and `0.9.106`);
  `test_pinned_upstream_version_is_supported` fails on any other installed version.
- **Parity tests** (`tests/test_traversal.py`): for BFS and DFS, the trace's seeds equal the `Start: [...]` of upstream's
  own `_query_graph_text`, its node count equals `N nodes found`, and its text equals upstream's body byte for byte;
  DFS replay never falls back; BFS depths are non-decreasing; every non-seed node has exactly one discovery edge.
  Verified on a real graph too (vionix-engram's code graph, 4 sentence questions × BFS/DFS: identical seeds and counts).
- Likely upstream changes and their failure mode: a renamed/re-signed helper → `AttributeError`/`TypeError` at import
  or call (loud); a changed `_bfs`/`_dfs` recording rule → parity/fallback tests fail; a changed seed rule → seed
  parity fails. None of them can silently diverge from `query_graph` without a test going red.
- Cost of a bump: run the tests in the new image; usually zero changes; a red test is a small port in `core.py`.

```bash
docker run --rm --user 0 -v "$PWD/ohanalabs:/o" --entrypoint sh ghcr.io/ohanalabs-ai/graphify/graphify-mcp \
  -c 'pip install -q pytest httpx && cd /tmp && python -m pytest -q -p no:cacheprovider /o/traversal/tests'
```

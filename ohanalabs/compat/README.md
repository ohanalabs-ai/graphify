# OLD vs NEW graphify — equivalence against Engram's contract

**OLD** = `ghcr.io/marcellodesales/graphify-service:main-55dbd00` — graphifyy **0.9.18**, what Engram runs today (k8s-infra
`engram-graphify-mcp`, and the `FROM` of vionix-engram's worker images). Not 0.1.14: `pip show graphifyy` in that image
says 0.9.18. **NEW** = this branch's images, upstream **0.9.106**.

`compare.py` runs, with both images and into separate copies of each sample repo (untrusted data: shallow-cloned into
fresh temp dirs, only ever parsed):
- vionix-engram's worker pipeline — `extract <repo> --code-only`, then `cluster-only`, `export graphml`,
  `export callflow-html`, `export svg` (`internal/graphify/run.go`);
- its memory pipeline — `merge-graphs <label>/graphify-out/graph.json … --out <memory>/graphify-out/graph.json`
  (`internal/memory/pipeline.go`);
- both MCP servers the way k8s-infra runs them, called exactly like vionix-engram's `mcpproxy`, for every
  server × graph pair — incl. the NEW server reading OLD-built graphs (the existing Engram PVC) — plus Engram's Bruno
  contract cases at the MCP level.

```bash
python3 ohanalabs/compat/compare.py --new-cli ghcr.io/ohanalabs-ai/graphify/graphify:<tag> \
  --new-mcp ghcr.io/ohanalabs-ai/graphify/graphify-mcp:<tag> \
  --repo vionix-engram=https://github.com/ohanalabs-ai/vionix-engram.git \
  --repo itsdangerous=https://github.com/pallets/itsdangerous.git \
  --probe vionix-engram:CallTool --probe vionix-engram:Extract --probe vionix-engram:Merge \
  --probe itsdangerous:Signer --probe itsdangerous:URLSafeSerializer --out ohanalabs/compat/RESULTS.md
```

Plus vionix-engram's **own** MCP integration test, unchanged, with the image swapped (amd64 build of the NEW server):
`GRAPHIFY_MCP_IMAGE=<img> DOCKER_CONFIG_PATH=$HOME/.docker go test -tags integration -run TestMCP_DependencyQuery ./internal/devsecops/`

## Verdict (2026-11-01, vionix-engram a4f5fe6 · itsdangerous 672971d)

| Area | Result | Breaking? |
|---|---|---|
| Engram `TestMCP_DependencyQuery` (11 cases: npm/go/cargo versions, DevSecOps phases, OCI images, CRDs, deprecated APIs, CVEs, severities — golden graph) | **11/11 PASS on OLD and on NEW** | no |
| Bruno contract cases at MCP level (`07-query` "main", memory `04` "main", `05` graph_stats, 2-repo `03` "image"), every server × graph | all pass (see RESULTS.md) — but see the next row | — |
| **Bare-keyword questions** ("main", "image") | OLD traverses; **NEW refuses**: `Ambiguous: 'main' matches 5 nodes in different files.` (upstream #4511), `isError=false`. Bruno 07 still passes only because the refusal text contains "main" | **YES — behavioural.** Sentence questions are unaffected (all 11 Engram cases). Engram callers should send sentences or explicit seeds; Engram could surface the refusal as a clarification |
| MCP request/response shape, tool names, `project_path` → `<project>/graphify-out/graph.json` | unchanged | no |
| Answer text format | NEW prepends `Graph: <path> (<N> nodes) \| …` to `query_graph` answers; the path is ABSOLUTE when the graph is outside the server CWD (in k8s: `/graphify-service/repos/<id>/graphify-out/graph.json`), and Engram passes answers to clients verbatim | minor: an additive header line + an internal path shown to API users |
| Extraction | all steps rc=0 on both; NEW finds **+55% nodes / +68% edges** (Go: 1015→1569 / 2046→3437; Python: 233→364 / 544→691): new `contains` (file→symbol) and Go `imports_from` edges, `external` stdlib nodes (e.g. `testing.T`, `net/http.Request`), more `concept` nodes; extra node attributes (`_callable`, `_go_fields`, `external`, `version`, …) — additive. Same probe symbols present on both | no (richer); communities and top-degree differ |
| `merge-graphs` | rc=0 on both; repo tags present (`vionix-engram`, `itsdangerous`) on both; NEW leaves **54 shared external package nodes** (`go_pkg_*`, `file_type: concept`, `external: true`) **untagged and un-prefixed** | no for Engram: `correlationKey` skips non-`code` nodes. Watch: shared stdlib *code* nodes (e.g. `net/http.Request`) can add cross-repo correlation edges between two Go repos |
| Backward compat (NEW server ← OLD-built graphs, the current PVC) | reads them; `graph_stats` + `query_graph` answer | no |
| Forward compat (OLD server ← NEW-built graphs) | reads them | no |
| Runtime contract (uid/gid 10001, `/opt/venv`, `/workspace`, Debian slim, Python 3.12) | identical; NEW adds `git`; NEW is **multi-arch** (OLD was amd64-only) | no |

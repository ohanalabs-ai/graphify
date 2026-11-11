# OhanaLabs Vionix integration of graphify

This fork (`ohanalabs-ai/graphify`, from `Graphify-Labs/graphify`) builds the graphify images OhanaLabs' Engram runs.
Upstream code is untouched; every OhanaLabs file lives **outside the `graphify/` package**:

| Path | What |
|---|---|
| `docker-compose.yaml` (root) | the two image services + a `smoke` profile |
| `ohanalabs/docker/graphify.Dockerfile` | **`ghcr.io/ohanalabs-ai/graphify/graphify`** — the CLI/runtime; vionix-engram `worker` and `memoryworker` build `FROM` it (`graphify extract --code-only`, `merge-graphs`, `cluster-only`, `export …`) |
| `ohanalabs/docker/graphify-mcp.Dockerfile` | **`ghcr.io/ohanalabs-ai/graphify/graphify-mcp`** — the MCP query server (`python -m graphify.serve`, Streamable HTTP, multi-project via `project_path`); Engram's `engram-graphify-mcp`. Also carries the opt-in `ohanalabs-traversal` |
| `ohanalabs/docker/smoke.sh` | extract a fixture with the CLI image, then `graph_stats` + `query_graph` with `project_path` against the MCP image — Engram's exact request |
| `ohanalabs/compat/` | OLD (0.9.18) vs NEW equivalence harness + results |
| `ohanalabs/traversal/` | OhanaLabs-only structured traversal for a live UI animation (does not change `query_graph`) |
| `.github/workflows/vionix-*.yaml` | image builds (shared reusable `ohanalabs-ai/github-platform` `docker-multiarch-cicd.yaml`) + the smoke/traversal tests |

## Branches

- **`vionix/main`** — the integration branch = an upstream **release tag** (today `v0.9.106`) + the OhanaLabs files.
  Images: `ghcr.io/ohanalabs-ai/graphify/<image>:vionix-main-<sha7>` (the reusable slugifies `vionix/main`), `graphify` is
  **linux/amd64 only by default** (owner 2026-11-01: a Mac runs it through its Linux VM; dispatch the workflow with
  `platforms=linux/amd64,linux/arm64` when an arm64 build is really needed), `graphify-mcp` multi-arch, with SBOM + SLSA provenance. Pin them by that tag (+ digest where a mechanism needs one).
- Feature work → PRs into `vionix/main` (PR images `…/<image>/pr/<n>:<sha7>`, deleted on close).
- `v8` mirrors upstream; never commit OhanaLabs files there; never push to `Graphify-Labs/*`.

## The Engram contract (must not change on an upstream bump)

| Contract | Where Engram depends on it |
|---|---|
| CLI `graphify extract <repo> --code-only` → `<repo>/graphify-out/graph.json`; `cluster-only`, `export graphml|callflow-html|svg` | vionix-engram `internal/graphify/run.go` (worker) |
| `graphify merge-graphs <label>/graphify-out/graph.json … --out <memory>/graphify-out/graph.json`, tagging nodes `repo=<label>` (grandparent dir) | `internal/memory/pipeline.go` + `correlate.go` (`sourceKey` reads `repo`) |
| MCP `POST /mcp` JSON-RPC `tools/call` **without initialize**, `Accept: application/json, text/event-stream`, `--stateless --json-response` → JSON `result.content[].text` | `internal/mcpproxy/mcpproxy.go` |
| tool `query_graph {project_path, question}` and `graph_stats {project_path}`; `<project_path>/graphify-out/graph.json` is the cached graph read | `internal/api/handlers.go`, `memory_handlers.go`; Bruno suites `tests/integration/bruno*/` |
| answer text contains the asked-about labels | `internal/devsecops/mcp_integration_test.go` (`TestMCP_DependencyQuery`, 11 cases) |
| runtime: Debian slim, Python 3.12, venv `/opt/venv`, uid/gid **10001**, `WORKDIR /workspace` | worker Dockerfiles (`USER 10001:10001`), the existing PVC's file ownership |

`ohanalabs/compat/RESULTS.md` is the evidence for the current pin.

## Sync with upstream (a new graphify release)

1. `git fetch upstream --tags` (`upstream` = `https://github.com/Graphify-Labs/graphify.git`); pick the release tag `vX.Y.Z`.
2. Branch `sync/vX.Y.Z` from `vX.Y.Z`; `git checkout origin/vionix/main -- docker-compose.yaml ohanalabs .github/workflows/vionix-*.yaml`
   (OhanaLabs files only — they never touch `graphify/`, so this is conflict-free); bump `GRAPHIFY_UPSTREAM_VERSION`
   in `docker-compose.yaml`.
3. Run the gates locally, then open the PR into `vionix/main` (its CI runs the smoke + traversal tests):
   - `docker compose -f docker-compose.yaml build && docker compose -f docker-compose.yaml --profile smoke run --rm smoke`
   - the traversal parity tests (`ohanalabs/traversal/README.md`) — on a red parity test, fix
     `ohanalabs_traversal/core.py` against the new internals, then add `X.Y.Z` to `SUPPORTED_UPSTREAM`;
   - `python3 ohanalabs/compat/compare.py …` (README there) and **vionix-engram's own MCP test** against the new image:
     `GRAPHIFY_MCP_IMAGE=<new image> DOCKER_CONFIG_PATH=$HOME/.docker go test -tags integration -run TestMCP_DependencyQuery ./internal/devsecops/`
     (the compose there pins `platform: linux/amd64`, so use an amd64 image).
4. Merge → `vionix-main-<sha7>`; then the consumers pin it (vionix-engram `GRAPHIFY_IMAGE`, k8s-infra `engram-graphify-mcp`).

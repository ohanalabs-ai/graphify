"""ohanalabs_traversal tests. The PARITY tests are the upgrade guard: the structured trace must pick the SAME seeds and
reach the SAME number of nodes as upstream's own `query_graph` text path (`graphify.serve._query_graph_text`) — if an
upstream bump changes the internals this module calls, these fail before anything ships.

Run inside the graphify-mcp image (has graphify + starlette + httpx via mcp):
  docker run --rm --user 0 -v "$PWD/ohanalabs:/o" --entrypoint sh ghcr.io/ohanalabs-ai/graphify/graphify-mcp \
    -c 'pip install -q pytest && PYTHONPATH=/o/traversal pytest -q /o/traversal/tests'
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import networkx as nx
import pytest
from graphify import serve as _s
from networkx.readwrite import json_graph

from ohanalabs_traversal import core as T

# A small, deterministic code graph shaped like graphify's own output (node-link JSON, directed, communities).
NODES = [
    ("svc_main", {"label": "main()", "file_type": "code", "source_file": "cmd/main.go", "community": 0}),
    ("svc_server", {"label": "Server", "file_type": "code", "source_file": "api/server.go", "community": 0}),
    ("svc_handle_query", {"label": ".handleQuery()", "file_type": "code", "source_file": "api/handlers.go", "community": 0}),
    ("svc_calltool", {"label": ".CallTool()", "file_type": "code", "source_file": "mcpproxy/mcpproxy.go", "community": 1}),
    ("svc_extract", {"label": "Extract()", "file_type": "code", "source_file": "graphify/run.go", "community": 1}),
    ("svc_merge", {"label": "Merge()", "file_type": "code", "source_file": "graphify/run.go", "community": 1}),
    ("svc_pipeline", {"label": "Assemble()", "file_type": "code", "source_file": "memory/pipeline.go", "community": 2}),
    ("svc_correlate", {"label": "correlationKey()", "file_type": "code", "source_file": "memory/correlate.go", "community": 2}),
]
EDGES = [
    ("svc_main", "svc_server", "calls"), ("svc_server", "svc_handle_query", "method"),
    ("svc_handle_query", "svc_calltool", "calls"), ("svc_pipeline", "svc_merge", "calls"),
    ("svc_pipeline", "svc_correlate", "calls"), ("svc_main", "svc_pipeline", "calls"),
    ("svc_extract", "svc_merge", "references"),
]
QUESTIONS = [
    "how does handleQuery call the MCP CallTool?",
    "what does Assemble do with Merge and correlationKey?",
    "where is the graph extracted?",
]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    G = nx.DiGraph()
    for nid, d in NODES:
        G.add_node(nid, id=nid, norm_label=d["label"].lower(), **d)
    for u, v, rel in EDGES:
        G.add_edge(u, v, relation=rel, confidence="EXTRACTED", source_file=G.nodes[u]["source_file"])
    out = tmp_path / "proj" / "graphify-out"
    out.mkdir(parents=True)
    (out / "graph.json").write_text(json.dumps(json_graph.node_link_data(G, edges="links")))
    return tmp_path / "proj"


def _upstream(G, question: str, mode: str = "bfs", depth: int = 3) -> tuple[list[str], int]:
    text = _s._query_graph_text(G, question, mode=mode, depth=depth)
    m_start = re.search(r"Start: (\[.*?\])", text)
    m_found = re.search(r"(\d+) nodes found", text)
    assert m_start and m_found, text[:300]
    return eval(m_start.group(1), {}, {}), int(m_found.group(1))  # noqa: S307 — a python list literal of labels


def test_pinned_upstream_version_is_supported():
    assert T.upstream_version() in T.SUPPORTED_UPSTREAM, (
        f"graphifyy {T.upstream_version()} is not in SUPPORTED_UPSTREAM {T.SUPPORTED_UPSTREAM}: re-run these parity "
        "tests against the new version, then add it")


@pytest.mark.parametrize("mode", ["bfs", "dfs"])
@pytest.mark.parametrize("question", QUESTIONS)
def test_parity_with_query_graph(project: Path, question: str, mode: str):
    G, path = T.load(str(project), root=str(project.parent))
    want_seeds, want_count = _upstream(G, question, mode=mode)
    got = T.trace(G, question, mode=mode)
    assert got["status"] == "ok"
    assert [s["label"] for s in got["seeds"]] == want_seeds
    assert got["node_count"] == want_count == len(got["nodes"])
    # the text the trace carries is byte-identical to what query_graph renders for those seeds (graph header aside)
    assert got["text"].split("\n\n", 1)[1] == _s._query_graph_text(G, question, mode=mode).split("\n\n", 1)[1]


def test_bfs_order_depth_and_tree(project: Path):
    G, _ = T.load(str(project))
    got = T.trace(G, "how does handleQuery call the MCP CallTool?", mode="bfs", depth=3)
    nodes = got["nodes"]
    depths = [n["depth"] for n in nodes]
    assert depths == sorted(depths), "BFS visits must come in non-decreasing depth"
    seed_ids = {s["id"] for s in got["seeds"]}
    assert all(n["depth"] == 0 for n in nodes if n["id"] in seed_ids)
    disc = [e for e in got["edges"] if e["discovery"]]
    assert len(disc) == len(nodes) - len(seed_ids), "every non-seed node is discovered by exactly one edge"
    ids = [n["id"] for n in nodes]
    for e in disc:  # a discovery edge's parent was visited before its child
        assert ids.index(e["from"]) < ids.index(e["to"]) and nodes[e["step"]]["id"] == e["to"]
    assert all("community" in n for n in nodes)
    assert all(e["relation"] for e in disc)


def test_refusal_and_no_match_are_reported_not_raised(project: Path):
    G, _ = T.load(str(project))
    assert T.trace(G, "zzzz-qqqq-nothing")["status"] == "no_match"


def test_root_is_enforced(project: Path, tmp_path: Path):
    with pytest.raises(PermissionError):
        T.graph_path_for(str(project), root=str(tmp_path / "elsewhere"))


def test_http_and_sse(project: Path):
    from starlette.testclient import TestClient

    from ohanalabs_traversal.http import build_app

    c = TestClient(build_app(str(project.parent), api_key="k"))
    assert c.get("/healthz").status_code == 200
    body = {"project_path": str(project), "question": "what does Assemble do with Merge and correlationKey?"}
    assert c.post("/v1/trace", json=body).status_code == 401
    r = c.post("/v1/trace", json=body, headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["nodes"]
    assert c.post("/v1/trace", json={**body, "project_path": "/etc"}, headers={"X-API-Key": "k"}).status_code == 403
    s = c.get("/v1/trace/stream", params=body, headers={"X-API-Key": "k"})
    events = re.findall(r"^event: (\w+)$", s.text, re.M)
    assert events[0] == "start" and events[-1] == "done" and "seed" in events


@pytest.mark.parametrize("question", QUESTIONS)
def test_dfs_replay_matches_upstream_and_respects_depth(project: Path, question: str):
    G, _ = T.load(str(project))
    got = T.trace(G, question, mode="dfs", depth=2)
    assert all(n["depth"] is not None for n in got["nodes"]), "DFS replay diverged from upstream _dfs (fell back)"
    assert max(n["depth"] for n in got["nodes"]) <= 2


@pytest.mark.parametrize("mode", ["bfs", "dfs"])
@pytest.mark.parametrize("question", QUESTIONS)
def test_parity_with_query_graph_no_doc_demotion(project: Path, question: str, mode: str):
    """The opt-out (`demote_docs=False` ≙ upstream MCP `demote_docs:false` / CLI `--no-doc-demotion`) must stay in
    lockstep with upstream too (upstream ≥ 0.9.101 ranks source above tests/docs by default)."""
    G, _ = T.load(str(project), root=str(project.parent))
    want = _s._query_graph_text(G, question, mode=mode, demote_docs=False)
    got = T.trace(G, question, mode=mode, demote_docs=False)
    assert got["status"] == "ok"
    assert got["text"].split("\n\n", 1)[1] == want.split("\n\n", 1)[1]

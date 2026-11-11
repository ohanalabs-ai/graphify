"""Structured graph traversal for a LIVE traversal animation — the same seeds and the same BFS/DFS `query_graph` uses,
returned as data instead of text.

Upstream `query_graph` (graphify/serve.py) answers with TEXT only. Internally it picks seeds
(`_query_name_refusal` → `_query_terms` → `_score_query` → `_pick_seeds`) and traverses a context-filtered undirected
view (`_resolve_context_filters` → `_filter_graph_by_context(_traversal_view(G))` → `_bfs`/`_dfs`). `_bfs`/`_dfs`
return `visited` (a set) and `edges_seen` — the (parent, child) discovery edges IN DISCOVERY ORDER, followed by the
induced edges `_complete_induced_edges` appends. This module calls those SAME module-level functions (it never
re-implements scoring) and replays `edges_seen` to recover the visit order, each node's depth and parent, which edges
discovered a node and which were only induced — what a UI needs to animate the walk step by step.

It does NOT change `query_graph`, the MCP server, or anything under `graphify/`. It depends on PRIVATE upstream
functions, so it is pinned: `SUPPORTED_UPSTREAM` lists the graphifyy versions whose internals it was verified against,
and tests/test_traversal.py proves PARITY with upstream's own `_query_graph_text` (same seeds, same node count) — run it on
every upstream bump (ohanalabs/README-VIONIX.md → sync procedure).
"""
from __future__ import annotations

import os
from importlib import metadata
from pathlib import Path
from typing import Any

from graphify import paths as _paths
from graphify import serve as _s

SUPPORTED_UPSTREAM = ("0.9.100", "0.9.106")


def upstream_version() -> str:
    try:
        return metadata.version("graphifyy")
    except metadata.PackageNotFoundError:  # running from a source checkout
        return "unknown"


def graph_path_for(project_path: str, root: str | None = None) -> str:
    """`<project_path>/<GRAPHIFY_OUT>/graph.json` — the same cached graph upstream's MCP resolves for `project_path`.
    With `root`, the resolved project must live under it (no reads outside the served volume)."""
    p = Path(project_path).resolve()
    if root is not None:
        r = Path(root).resolve()
        if p != r and r not in p.parents:
            raise PermissionError(f"project_path {project_path!r} is outside the served root {root!r}")
    return str(p / _paths.GRAPHIFY_OUT / "graph.json")


def load(project_path: str, root: str | None = None):
    path = graph_path_for(project_path, root)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no graph at {path} (run `graphify extract` for that project first)")
    return _s._load_graph(path), path


def select_seeds(G, question: str, *, demote_docs: bool = True) -> dict[str, Any]:
    """Exactly upstream `_query_graph_text`'s keyword path (refusal → terms → scoring → intent guard → seeds).

    Since upstream 0.9.101+ the scoring is path-weighted: tests, docs, config files and file-less nodes count
    `_QUERY_DEMOTE_WEIGHT` (source ranks above them) unless the question asks about them. `demote_docs=True` is
    upstream's default (the MCP `demote_docs` argument / CLI `--no-doc-demotion` is its opposite); we follow it so the
    animation shows the walk `query_graph` performs by default. The scores are returned (`scores`) because upstream
    also uses them to order the rendered text (`_subgraph_to_text(scores=)`)."""
    refusal = _s._query_name_refusal(G, question)
    if refusal:
        return {"status": "refused", "message": refusal, "seeds": [], "terms": [], "scores": {}}
    terms = _s._query_terms(question)
    qs = _s._score_query(G, terms, collect_per_term_seeds=True,
                         path_weight=_s._query_path_weight(terms, demote_docs=demote_docs))
    best = qs.best_seed_by_term
    intent = {t for t in best if t in _s._RELATIONAL_INTENT_TERMS}
    if intent and any(t not in _s._RELATIONAL_INTENT_TERMS for t in terms):
        best = {t: nid for t, nid in best.items() if t not in intent}
    seeds = _s._pick_seeds(qs.ranked, G=G, best_seed_by_term=best, term_order=qs.term_order)
    if not seeds:
        return {"status": "no_match", "message": _s._QUERY_NO_MATCH, "seeds": [], "terms": terms, "scores": {}}
    return {"status": "ok", "message": "", "seeds": list(seeds), "terms": terms,
            "scores": {nid: sc for sc, nid in qs.ranked}}


def _node(G, nid: str, depth: int | None, parent: str | None, step: int) -> dict[str, Any]:
    d = G.nodes[nid] if nid in G.nodes else {}
    return {
        "step": step,
        "id": nid,
        "label": d.get("label", nid),
        "depth": depth,
        "parent": parent,
        "community": d.get("community"),
        "community_name": d.get("community_name"),
        "file_type": d.get("file_type"),
        "source_file": d.get("source_file"),
        "source_location": d.get("source_location"),
        "repo": d.get("repo"),
    }


def _edge(TG, u: str, v: str, *, discovery: bool, step: int | None) -> dict[str, Any]:
    data = TG.get_edge_data(u, v) or {}
    if data and isinstance(next(iter(data.values()), None), dict) and "relation" not in data:  # multigraph
        data = next(iter(data.values()))
    src, tgt = data.get("_src", u), data.get("_tgt", v)  # true direction kept by _traversal_view
    return {"from": u, "to": v, "source": src, "target": tgt, "relation": data.get("relation"),
            "confidence": data.get("confidence"), "discovery": discovery, "step": step}


def trace(G, question: str, *, mode: str = "bfs", depth: int = 3, context_filters: list[str] | None = None,
          token_budget: int = 2000, graph_path: str | None = None, with_text: bool = True,
          demote_docs: bool = True) -> dict[str, Any]:
    """Seeds + ordered visits (depth, parent, community) + edges for `question` — the walk `query_graph` performs."""
    if mode not in ("bfs", "dfs"):
        raise ValueError("mode must be bfs or dfs")
    sel = select_seeds(G, question, demote_docs=demote_docs)
    out: dict[str, Any] = {"question": question, "mode": mode, "depth": depth, "status": sel["status"],
                           "message": sel["message"], "terms": sel["terms"], "upstream": upstream_version(),
                           "seeds": [], "nodes": [], "edges": []}
    if sel["status"] != "ok":
        return out
    seeds = sel["seeds"]
    filters, filter_source = _s._resolve_context_filters(question, context_filters)
    TG = _s._filter_graph_by_context(_s._traversal_view(G), filters)
    visited, edges_seen = (_s._dfs if mode == "dfs" else _s._bfs)(TG, seeds, depth)

    if mode == "dfs":
        order, edges = _replay_dfs(G, TG, seeds, depth, visited)
    else:
        order, edges = _replay_bfs(G, TG, seeds, edges_seen, visited)

    out.update({
        "seeds": [{"id": s, "label": G.nodes[s].get("label", s)} for s in seeds],
        "nodes": order,
        "edges": edges,
        "context": {"filters": filters, "source": filter_source},
        "node_count": len(visited),
    })
    if with_text:  # the exact text query_graph would return for these seeds, to show next to the animation
        out["text"] = _s._render_query(G, question, seeds, mode=mode, depth=depth, token_budget=token_budget,
                                       context_filters=context_filters, graph_path=graph_path, scores=sel["scores"])
    return out

def _hub_threshold(TG) -> int:
    """The same hub cut-off upstream _bfs/_dfs compute (p99 degree, floored at 50)."""
    degrees = sorted(TG.degree(n) for n in TG.nodes())
    return max(50, degrees[int(len(degrees) * 0.99)]) if degrees else 50


def _replay_bfs(G, TG, seeds, edges_seen, visited):
    """Exact for BFS: upstream records (parent, child) only when the child joins the next frontier, in that order."""
    depth_of: dict[str, int] = {}
    order: list[dict[str, Any]] = []
    for s in seeds:
        if s not in depth_of:
            depth_of[s] = 0
            order.append(_node(G, s, 0, None, len(order)))
    edges: list[dict[str, Any]] = []
    for u, v in edges_seen:
        if v not in depth_of and u in depth_of and v in visited:
            depth_of[v] = depth_of[u] + 1
            order.append(_node(G, v, depth_of[v], u, len(order)))
            edges.append(_edge(TG, u, v, discovery=True, step=len(order) - 1))
        elif u in visited and v in visited:  # an induced edge between visited nodes (from _complete_induced_edges)
            edges.append(_edge(TG, u, v, discovery=False, step=None))
    for nid in sorted(visited - set(depth_of)):  # defensive: visited but never discovered via an edge
        order.append(_node(G, nid, None, None, len(order)))
    return order, edges


def _replay_dfs(G, TG, seeds, depth, visited):
    """DFS: upstream records an edge when it PUSHES a neighbour (some pushes are dropped at pop time), so the true
    visit order and stack depth are replayed here with upstream's own rule set (LIFO stack of (node, d), skip if
    visited or d > depth, don't expand hubs except seeds) — and VERIFIED against upstream's `visited` on every call: on
    any mismatch (an upstream change to _dfs) it falls back to the BFS-style replay of push order with depth None."""
    hub, seed_set = _hub_threshold(TG), set(seeds)
    seen: set[str] = set()
    order: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    stack = [(n, 0, None) for n in reversed(seeds)]
    while stack:
        node, d, parent = stack.pop()
        if node in seen or d > depth:
            continue
        seen.add(node)
        order.append(_node(G, node, d, parent, len(order)))
        if parent is not None:
            edges.append(_edge(TG, parent, node, discovery=True, step=len(order) - 1))
        if node not in seed_set and TG.degree(node) >= hub:
            continue
        for nb in TG.neighbors(node):
            if nb not in seen:
                stack.append((nb, d + 1, node))
    if seen != set(visited):  # upstream's _dfs changed: never show a walk that differs from the answer's
        _, edges_seen = _s._dfs(TG, seeds, depth)
        order, edges = _replay_bfs(G, TG, seeds, edges_seen, visited)
        for n in order:
            n["depth"] = None
        return order, edges
    known = {(e["from"], e["to"]) for e in edges} | {(e["to"], e["from"]) for e in edges}
    for u, v in TG.subgraph(visited).edges():  # the induced edges the answer text also includes
        if (u, v) not in known:
            edges.append(_edge(TG, u, v, discovery=False, step=None))
    return order, edges

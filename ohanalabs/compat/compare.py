#!/usr/bin/env python3
"""OLD vs NEW graphify image equivalence test for OhanaLabs' Engram (ohanalabs/compat/README.md).

OLD = ghcr.io/marcellodesales/graphify-service:main-55dbd00 (graphifyy 0.9.18 — what Engram runs today)
NEW = the images this branch builds (graphify / graphify-mcp, upstream 0.9.106)

For every sample repository it runs, with BOTH CLI images and into SEPARATE copies, exactly what vionix-engram's
worker does (internal/graphify/run.go): `graphify extract <repo> --code-only`, then the best-effort enrich steps
(`cluster-only`, `export graphml`, `export callflow-html`, `export svg`), and memoryworker's `merge-graphs`.
Then it starts BOTH MCP servers the way k8s-infra runs engram-graphify-mcp (`python -m graphify.serve --transport http
--stateless --json-response`) and calls them exactly like vionix-engram's mcpproxy (bare JSON-RPC tools/call, no
initialize, Accept "application/json, text/event-stream", arguments {project_path, question}) for every
server × graph combination — including the NEW server reading OLD-built graphs (the existing Engram PVC).

Sample repositories are UNTRUSTED data: each is a shallow clone into its own new temp dir, never executed — graphify
only parses them. Usage:
  python3 ohanalabs/compat/compare.py --new-cli <img> --new-mcp <img> [--old <img>] --repo <name>=<git url> ... --out RESULTS.md
Docker must be available. The OLD image is amd64-only (runs emulated on arm64 hosts).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

OLD_DEFAULT = "ghcr.io/marcellodesales/graphify-service:main-55dbd00"
QUESTIONS = ["what are the main entry points?", "how is the graph extracted?"]


def sh(args: list[str], check: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=timeout)


def run_cli(image: str, platform: str | None, workdir: str, args: list[str]) -> tuple[int, str]:
    cmd = ["docker", "run", "--rm"] + (["--platform", platform] if platform else []) + [
        "-v", f"{workdir}:/workspace/repo", "-w", "/workspace/repo", image] + args
    p = sh(cmd, check=False)
    return p.returncode, (p.stdout + p.stderr)[-2000:]


def pipeline(image: str, platform: str | None, repo_dir: str) -> dict:
    t0 = time.time()
    rc, out = run_cli(image, platform, repo_dir, ["extract", "/workspace/repo", "--code-only"])
    res = {"extract_rc": rc, "extract_s": round(time.time() - t0, 1), "extract_tail": out[-400:] if rc else ""}
    enrich = {}
    for step in (["cluster-only", "/workspace/repo"], ["export", "graphml", "/workspace/repo"],
                 ["export", "callflow-html", "/workspace/repo"], ["export", "svg", "/workspace/repo"]):
        erc, _ = run_cli(image, platform, repo_dir, step)
        enrich[" ".join(step[:-1])] = "ok" if erc == 0 else f"rc={erc}"
    res["enrich"] = enrich
    res["artifacts"] = sorted(os.listdir(os.path.join(repo_dir, "graphify-out"))) if os.path.isdir(os.path.join(repo_dir, "graphify-out")) else []
    return res


def graph_summary(path: str, probes: list[str]) -> dict:
    g = json.load(open(path))
    nodes, links = g.get("nodes", []), g.get("links", g.get("edges", []))
    deg = collections.Counter()
    for e in links:
        deg[e.get("source")] += 1
        deg[e.get("target")] += 1
    label = {n["id"]: n.get("label", n["id"]) for n in nodes}
    labels = [n.get("label", "") for n in nodes]
    return {
        "nodes": len(nodes),
        "edges": len(links),
        "communities": len({n.get("community") for n in nodes if n.get("community") is not None}),
        "file_types": dict(collections.Counter(n.get("file_type", "?") for n in nodes).most_common()),
        "relations": dict(collections.Counter(e.get("relation", "?") for e in links).most_common(8)),
        "files": len({n.get("source_file") for n in nodes if n.get("source_file")}),
        "top_degree": [label.get(i, i) for i, _ in deg.most_common(10)],
        # a probe matches a label that CONTAINS it (Go methods are labelled `CallTool()` / `.CallTool()`)
        "probes_present": {p: sorted({l for l in labels if p in l})[:3] or False for p in probes},
        "node_keys": sorted({k for n in nodes for k in n.keys()}),
    }


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port


def start_mcp(image: str, platform: str | None, mount: str, boot_graph: str | None) -> tuple[str, str]:
    port = free_port()
    name = f"gf-compat-{port}"
    cmd = ["docker", "run", "-d", "--rm", "--name", name] + (["--platform", platform] if platform else []) + [
        "-p", f"127.0.0.1:{port}:8080", "-v", f"{mount}:/workspace/data", "--entrypoint", "python", image,
        "-m", "graphify.serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8080", "--stateless", "--json-response"]
    if boot_graph:
        cmd.append(boot_graph)
    sh(cmd)
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(120):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            time.sleep(1)
            return name, url
        except OSError:
            time.sleep(1)
    logs = sh(["docker", "logs", name], check=False)
    raise RuntimeError(f"MCP {image} never opened {port}: {logs.stdout[-500:]}{logs.stderr[-500:]}")


def call(url: str, tool: str, args: dict) -> dict:
    """Exactly vionix-engram internal/mcpproxy.CallTool."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=120))
    except Exception as exc:  # noqa: BLE001 — report transport failures as data
        return {"transport_error": str(exc)[:300]}
    if r.get("error"):
        return {"rpc_error": r["error"]}
    res = r.get("result") or {}
    text = "".join(p.get("text", "") for p in res.get("content", []) if p.get("type") == "text")
    return {"is_error": bool(res.get("isError")), "text": text}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=OLD_DEFAULT)
    ap.add_argument("--old-platform", default="linux/amd64")
    ap.add_argument("--new-cli", required=True)
    ap.add_argument("--new-mcp", required=True)
    ap.add_argument("--repo", action="append", required=True, help="name=git-url (shallow-cloned, never executed)")
    ap.add_argument("--probe", action="append", default=[], help="name:Symbol expected in that repo's graph")
    ap.add_argument("--out", default="ohanalabs/compat/RESULTS.md")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="graphify-compat-")
    report = {"old": a.old, "new_cli": a.new_cli, "new_mcp": a.new_mcp, "repos": {}}
    graphs = {"old": [], "new": []}
    try:
        for spec in a.repo:
            name, url = spec.split("=", 1)
            src = tempfile.mkdtemp(prefix=f"src-{name}-", dir=work)
            sh(["git", "clone", "-q", "--depth", "1", url, src], timeout=600)
            rev = sh(["git", "-C", src, "rev-parse", "--short", "HEAD"]).stdout.strip()
            probes = [p.split(":", 1)[1] for p in a.probe if p.startswith(name + ":")]
            entry = {"url": url, "rev": rev}
            for side, image, plat in (("old", a.old, a.old_platform), ("new", a.new_cli, None)):
                d = os.path.join(work, side, name)
                shutil.copytree(src, d, ignore=shutil.ignore_patterns(".git"))
                os.chmod(d, 0o777)
                entry[side] = pipeline(image, plat, d)
                gp = os.path.join(d, "graphify-out", "graph.json")
                if os.path.isfile(gp):
                    entry[side]["graph"] = graph_summary(gp, probes)
                    graphs[side].append(d)
            report["repos"][name] = entry
            print(f"[{name}] old nodes={entry['old'].get('graph', {}).get('nodes')} new nodes={entry['new'].get('graph', {}).get('nodes')}", flush=True)

        # merge-graphs (memoryworker) on each side
        for side, image, plat in (("old", a.old, a.old_platform), ("new", a.new_cli, None)):
            if len(graphs[side]) >= 2:
                base = os.path.join(work, side)
                ins = [f"/workspace/repo/{os.path.basename(d)}/graphify-out/graph.json" for d in graphs[side]]
                # Engram's memory layout (internal/memory/pipeline.go Assemble): inputs <label>/graphify-out/graph.json
                # (merge-graphs tags each node with the GRANDPARENT dir = the label), output
                # <memory>/graphify-out/graph.json — the cached graph query_graph reads via project_path=<memory>.
                os.makedirs(os.path.join(base, "memory", "graphify-out"), exist_ok=True)
                os.chmod(os.path.join(base, "memory"), 0o777); os.chmod(os.path.join(base, "memory", "graphify-out"), 0o777)
                rc, out = run_cli(image, plat, base, ["merge-graphs", *ins, "--out", "/workspace/repo/memory/graphify-out/graph.json"])
                mp = os.path.join(base, "memory", "graphify-out", "graph.json")
                m = {"rc": rc, **(graph_summary(mp, []) if rc == 0 and os.path.isfile(mp) else {"tail": out[-300:]})}
                if rc == 0 and os.path.isfile(mp):
                    g = json.load(open(mp))
                    tags = collections.Counter(n.get("repo") for n in g.get("nodes", []))
                    m["repo_tags"] = dict(tags.most_common())
                    m["id_prefixed"] = sum(1 for n in g.get("nodes", []) if "::" in str(n.get("id", ""))) == len(g.get("nodes", []))
                report[f"merge_{side}"] = m

        # MCP: each server × each graph (server mounts the whole work dir at /workspace/data)
        boot = os.path.join(work, "boot"); os.makedirs(boot, exist_ok=True)
        json.dump({"directed": False, "multigraph": False, "graph": {}, "nodes": [], "links": []}, open(os.path.join(boot, "graph.json"), "w"))
        servers = {}
        try:
            servers["old"] = start_mcp(a.old, a.old_platform, work, "/workspace/data/boot/graph.json")  # as k8s-infra runs it today
            servers["new"] = start_mcp(a.new_mcp, None, work, None)  # pure multi-project (no default graph)
            mcp = {}
            # Engram's own contract cases (vionix-engram tests/integration bruno*/), at the MCP level — the request its
            # mcpproxy sends for each: {project_path, question} / graph_stats, against the cached per-project graph.
            first = next(iter(report["repos"]))
            cases = [
                ("bruno/07-query", f"/workspace/data/{{g}}/{first}", "query_graph", {"question": "main"}, "main"),
                ("bruno-memory/04-query-graph", "/workspace/data/{g}/memory", "query_graph", {"question": "main"}, None),
                ("bruno-memory/05-graph-stats", "/workspace/data/{g}/memory", "graph_stats", {}, None),
                ("bruno-memory-2repos/03-image-query", "/workspace/data/{g}/memory", "query_graph", {"question": "image"}, None),
            ]
            engram = {}
            for sname, (_, url) in servers.items():
                for gside in ("old", "new"):
                    for cname, pp_t, tool, extra, must in cases:
                        r = call(url, tool, {"project_path": pp_t.format(g=gside), **extra})
                        ok = ("text" in r) and not r.get("is_error") and bool(r["text"].strip()) and (must is None or must in r["text"].lower())
                        engram[f"{sname}-server/{gside}-graph/{cname}"] = {"pass": ok, "first": first_line(r)}
            report["engram_cases"] = engram
            for sname, (_, url) in servers.items():
                for gside in ("old", "new"):
                    for name in report["repos"]:
                        pp = f"/workspace/data/{gside}/{name}"
                        key = f"{sname}-server/{gside}-graph/{name}"
                        stats = call(url, "graph_stats", {"project_path": pp})
                        qs = [call(url, "query_graph", {"project_path": pp, "question": q}) for q in QUESTIONS]
                        mcp[key] = {"graph_stats": stats, "query_graph": qs}
            report["mcp"] = mcp
        finally:
            for name, _ in servers.values():
                sh(["docker", "rm", "-f", name], check=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if a.json_out:
        json.dump(report, open(a.json_out, "w"), indent=2)
    write_markdown(report, a.out)
    print(f"wrote {a.out}")
    return 0


def first_line(r: dict) -> str:
    if "transport_error" in r:
        return f"❌ transport: {r['transport_error'][:80]}"
    if "rpc_error" in r:
        return f"❌ rpc: {str(r['rpc_error'])[:80]}"
    t = r.get("text", "").strip().splitlines()
    return ("⚠️ isError: " if r.get("is_error") else "") + (t[0][:110] if t else "(empty)")


def write_markdown(rep: dict, path: str) -> None:
    L = ["# OLD vs NEW graphify images — equivalence results", "",
         f"- OLD: `{rep['old']}` (graphifyy 0.9.18, what Engram runs today)",
         f"- NEW CLI: `{rep['new_cli']}` · NEW MCP: `{rep['new_mcp']}` (upstream 0.9.106)", "",
         "Generated by `ohanalabs/compat/compare.py` (same pipeline as vionix-engram's worker/memoryworker, same JSON-RPC "
         "request as its mcpproxy).", "", "## Extraction (`extract --code-only` + enrich)", "",
         "| repo | metric | OLD | NEW |", "|---|---|---|---|"]
    for name, e in rep["repos"].items():
        o, n = e["old"], e["new"]
        og, ng = o.get("graph", {}), n.get("graph", {})
        rows = [("rev", e["rev"], e["rev"]), ("extract rc / seconds", f"{o['extract_rc']} / {o['extract_s']}s", f"{n['extract_rc']} / {n['extract_s']}s"),
                ("nodes", og.get("nodes"), ng.get("nodes")), ("edges", og.get("edges"), ng.get("edges")),
                ("communities", og.get("communities"), ng.get("communities")), ("source files", og.get("files"), ng.get("files")),
                ("file_type histogram", og.get("file_types"), ng.get("file_types")), ("top relations", og.get("relations"), ng.get("relations")),
                ("top-10 degree", ", ".join(og.get("top_degree", [])[:10]), ", ".join(ng.get("top_degree", [])[:10])),
                ("probe symbols present", og.get("probes_present"), ng.get("probes_present")),
                ("enrich steps", o["enrich"], n["enrich"]), ("artifacts", ", ".join(o["artifacts"]), ", ".join(n["artifacts"])),
                ("node attributes", ", ".join(og.get("node_keys", [])), ", ".join(ng.get("node_keys", [])))]
        for k, a_, b_ in rows:
            L.append(f"| {name} | {k} | {a_} | {b_} |")
    L += ["", "## merge-graphs (memoryworker)", "", "| side | rc | nodes | edges |", "|---|---|---|---|"]
    for side in ("old", "new"):
        m = rep.get(f"merge_{side}", {})
        L.append(f"| {side} | {m.get('rc')} | {m.get('nodes')} | {m.get('edges')} |")
    L += ["", "### merged-graph contract (Engram correlate.go keys on `repo`; Bruno 02/03 need both repo slugs)", "",
          "| side | `repo` tags on merged nodes | every id `<tag>::<id>` |", "|---|---|---|"]
    for side in ("old", "new"):
        m = rep.get(f"merge_{side}", {})
        L.append(f"| {side} | {m.get('repo_tags')} | {m.get('id_prefixed')} |")
    L += ["", "## Engram's contract cases (Bruno suites, at the MCP level) — server × graph", "",
          "| server / graph / case | pass | answer (first line) |", "|---|---|---|"]
    for key, v in rep.get("engram_cases", {}).items():
        L.append(f"| {key} | {'✅' if v['pass'] else '❌'} | {v['first']} |")
    L += ["", "## MCP (`graph_stats`, `query_graph` with `project_path`) — server × graph", "",
          "| server / graph / repo | graph_stats | query_graph #1 | query_graph #2 |", "|---|---|---|---|"]
    for key, v in rep.get("mcp", {}).items():
        q = v["query_graph"]
        L.append(f"| {key} | {first_line(v['graph_stats'])} | {first_line(q[0])} | {first_line(q[1])} |")
    open(path, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())

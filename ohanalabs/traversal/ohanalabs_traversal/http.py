"""OhanaLabs traversal API — a SEPARATE small HTTP service (default :8081) next to the upstream MCP server (:8080).

  POST /v1/trace            {"project_path", "question", "mode"?: "bfs"|"dfs", "depth"?: 1..6, "demote_docs"?: true}  → the full trace (JSON)
  GET  /v1/trace/stream     ?project_path=&question=&mode=&depth=&delay_ms=  → Server-Sent Events for a live
                            animation: `seed` per seed, `visit` per newly discovered node (with its discovery edge),
                            `edge` per induced edge, then `done` (node_count + the exact query_graph text)
  GET  /healthz

The upstream MCP interface (`query_graph` & co. on /mcp) is untouched — this is an additional, OhanaLabs-only
surface. It reads the same cached graphs (<project_path>/graphify-out/graph.json) but only under TRAVERSAL_ROOT.
Auth: if OHANALABS_TRAVERSAL_API_KEY is set, every request must send it as `X-API-Key`.

  python -m ohanalabs_traversal.http --host 0.0.0.0 --port 8081 --root /graphify-service/repos
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from . import core as _t


def _flag(v, default: bool = True) -> bool:
    if v is None:
        return default
    return v if isinstance(v, bool) else str(v).lower() not in ("false", "0", "no")


def _params(d: dict) -> tuple[str, str, str, int, bool]:
    pp, q = str(d.get("project_path") or ""), str(d.get("question") or "")
    mode = str(d.get("mode") or "bfs")
    depth = int(d.get("depth") or 3)
    if not pp or not q:
        raise ValueError("project_path and question are required")
    if mode not in ("bfs", "dfs") or not 1 <= depth <= 6:
        raise ValueError("mode must be bfs|dfs and depth 1..6")
    return pp, q, mode, depth, _flag(d.get("demote_docs"))


def build_app(root: str, api_key: str | None = None) -> Starlette:
    def denied(request: Request):
        if api_key and not hmac.compare_digest(request.headers.get("x-api-key", ""), api_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    def run(pp: str, q: str, mode: str, depth: int, demote_docs: bool = True) -> dict:
        G, path = _t.load(pp, root)
        return _t.trace(G, q, mode=mode, depth=depth, graph_path=path, demote_docs=demote_docs)

    async def post_trace(request: Request):
        if (r := denied(request)):
            return r
        try:
            pp, q, mode, depth, dd = _params(await request.json())
            return JSONResponse(await asyncio.to_thread(run, pp, q, mode, depth, dd))
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        except FileNotFoundError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except (ValueError, json.JSONDecodeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def stream_trace(request: Request):
        if (r := denied(request)):
            return r
        try:
            pp, q, mode, depth, dd = _params(dict(request.query_params))
            delay = min(max(int(request.query_params.get("delay_ms", "0")), 0), 2000) / 1000
            res = await asyncio.to_thread(run, pp, q, mode, depth, dd)
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        except FileNotFoundError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        async def events():
            yield sse("start", {k: res[k] for k in ("question", "mode", "depth", "status", "message", "terms", "upstream")})
            if res["status"] == "ok":
                by_step = {e["step"]: e for e in res["edges"] if e["discovery"]}
                for n in res["nodes"]:
                    yield sse("seed" if n["depth"] == 0 else "visit", {"node": n, "edge": by_step.get(n["step"])})
                    if delay:
                        await asyncio.sleep(delay)
                for e in res["edges"]:
                    if not e["discovery"]:
                        yield sse("edge", e)
            yield sse("done", {"node_count": res.get("node_count", 0), "text": res.get("text", res["message"])})

        return StreamingResponse(events(), media_type="text/event-stream")

    async def healthz(_: Request):
        return JSONResponse({"ok": True, "upstream": _t.upstream_version(), "supported": list(_t.SUPPORTED_UPSTREAM)})

    return Starlette(routes=[Route("/v1/trace", post_trace, methods=["POST"]),
                             Route("/v1/trace/stream", stream_trace, methods=["GET"]),
                             Route("/healthz", healthz, methods=["GET"])])


def main() -> None:
    ap = argparse.ArgumentParser(prog="ohanalabs-traversal")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--root", default=os.environ.get("TRAVERSAL_ROOT", "/workspace"))
    a = ap.parse_args()
    import uvicorn

    uvicorn.run(build_app(a.root, os.environ.get("OHANALABS_TRAVERSAL_API_KEY") or None), host=a.host, port=a.port)


if __name__ == "__main__":
    main()

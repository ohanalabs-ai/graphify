# syntax=docker/dockerfile:1.7
# OhanaLabs Vionix integration — `graphify-mcp` image (ghcr.io/ohanalabs-ai/graphify/graphify-mcp).
#
# What it is: the official graphify MCP query server (`python -m graphify.serve`, the `graphify-mcp` console script)
# over the MCP Streamable HTTP transport, built from THIS checkout with ONLY the `mcp` extra (mcp + starlette +
# uvicorn) — no extraction extras, so it is small and starts fast. It serves graphs, it never builds them.
# Engram's engram-graphify-mcp Deployment runs it as a PURE MULTI-PROJECT server: no default graph is needed — every
# tool call carries `project_path` (Engram injects it), resolved to <project_path>/graphify-out/graph.json. The
# defaults below are the flags Engram's Deployment passes today (stateless, JSON responses, :8080, all interfaces).
# No graph is baked in; graphs come from the mounted volume. Runs as uid/gid 10001 like the CLI image, so it reads
# the existing Engram PVC files. Auth: optional `--api-key` / GRAPHIFY_API_KEY (header X-API-Key); in-cluster it runs
# behind NetworkPolicy.
#
# Build: docker compose -f docker-compose.yaml build graphify-mcp     (repo root; see ohanalabs/README.md)
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /src
COPY pyproject.toml README.md LICENSE LICENSE-MIT NOTICE /src/
COPY graphify /src/graphify
RUN pip install --upgrade pip setuptools wheel \
    && pip install ".[mcp]"

FROM ${PYTHON_IMAGE} AS runtime
ARG GRAPHIFY_UPSTREAM_VERSION=unknown
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="graphify-mcp" \
      org.opencontainers.image.description="Graphify MCP server, Streamable HTTP (OhanaLabs Vionix integration of Graphify-Labs/graphify)" \
      org.opencontainers.image.source="https://github.com/ohanalabs-ai/graphify" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${GRAPHIFY_UPSTREAM_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      ai.ohanalabs.upstream.repository="https://github.com/Graphify-Labs/graphify" \
      ai.ohanalabs.upstream.version="${GRAPHIFY_UPSTREAM_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN addgroup --gid 10001 app \
    && adduser --uid 10001 --gid 10001 --disabled-password --gecos "" app
USER 10001:10001
WORKDIR /workspace
EXPOSE 8080
ENTRYPOINT ["python", "-m", "graphify.serve"]
CMD ["--transport", "http", "--host", "0.0.0.0", "--port", "8080", "--stateless", "--json-response"]

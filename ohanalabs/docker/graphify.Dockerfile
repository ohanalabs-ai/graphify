# syntax=docker/dockerfile:1.7
# OhanaLabs Vionix integration — `graphify` CLI/runtime image (ghcr.io/ohanalabs-ai/graphify/graphify).
#
# What it is: the official Graphify-Labs/graphify CLI built from THIS checkout (branch vionix/main = an upstream release
# tag), with the curated extras OhanaLabs' Engram workers need. vionix-engram `worker` and `memoryworker` build
# `FROM` this image (ARG GRAPHIFY_IMAGE) and shell out to `graphify extract --code-only` and `graphify merge-graphs`.
# It replaces ghcr.io/marcellodesales/graphify-service (graphifyy 0.9.18, amd64-only) and keeps its runtime contract:
#   Debian slim · Python 3.12 · venv /opt/venv on PATH · user app uid/gid 10001 (owns the existing Engram PVC files)
#   · WORKDIR /workspace · ENTRYPOINT ["graphify"].
# Extras = the SAME set the 0.9.18 image shipped, so extraction output stays comparable (ohanalabs/compat/):
#   mcp (query server) · neo4j · watch · svg · leiden (graspologic communities) · pdf · office · google · postgres.
# git is added for the workers (the old image had none; memoryworker installed it itself).
#
# Build: docker compose -f docker-compose.yaml build graphify     (repo root; see ohanalabs/README.md)
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /src
COPY pyproject.toml README.md LICENSE LICENSE-MIT NOTICE /src/
COPY graphify /src/graphify
RUN pip install --upgrade pip setuptools wheel \
    && pip install ".[mcp,neo4j,watch,svg,leiden,pdf,office,google,postgres]" uvicorn

FROM ${PYTHON_IMAGE} AS runtime
ARG GRAPHIFY_UPSTREAM_VERSION=unknown
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="graphify" \
      org.opencontainers.image.description="Graphify CLI/runtime (OhanaLabs Vionix integration of Graphify-Labs/graphify)" \
      org.opencontainers.image.source="https://github.com/ohanalabs-ai/graphify" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${GRAPHIFY_UPSTREAM_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      ai.ohanalabs.upstream.repository="https://github.com/Graphify-Labs/graphify" \
      ai.ohanalabs.upstream.version="${GRAPHIFY_UPSTREAM_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN addgroup --gid 10001 app \
    && adduser --uid 10001 --gid 10001 --disabled-password --gecos "" app
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["graphify"]
CMD ["--help"]

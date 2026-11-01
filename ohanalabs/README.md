# ohanalabs/ — OhanaLabs additions to the Graphify fork

`ohanalabs-ai/graphify` is a fork of [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) (default
branch `v8`, tracking upstream releases). Everything OhanaLabs adds lives under this directory so the fork's diff
against upstream stays isolated and rebases stay trivial — nothing here is part of the `graphifyy` package.

| Path | What |
|---|---|
| `skills/graphify-graphiti-feeder/` | feed `graphify-out/` (+ the supply-chain `planeo-graph/`) into a Graphiti workspace on Neo4j via the Graphiti MCP — ported from the 0.1.x-era `marcellodesales/graphify` |

The supply-chain graph skill itself lives in ohanalabs-ai/vionix-engram (`skills/engram-memory-structure`).

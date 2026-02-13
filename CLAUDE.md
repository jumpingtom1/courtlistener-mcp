# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

courtlistener-mcp is a single-file MCP (Model Context Protocol) server that provides legal research tools backed by the CourtListener case law database. It exposes five tools (search_cases, lookup_citation, semantic_search, get_case_text, find_citing_cases) over stdio transport for use with Claude and other MCP-compatible clients.

## Running

```bash
# Install in editable mode
pip install -e .

# Run the server (uses stdio transport)
courtlistener-mcp
# or: python server.py

# Requires API token
export COURTLISTENER_API_TOKEN="your_token"
```

## Architecture

The entire server lives in `server.py` (~440 lines). Key structure:

- **FastMCP framework**: Server instance at module level (`mcp = FastMCP(...)`) with tool functions decorated via `@mcp.tool()`
- **Lifespan pattern**: `AppContext` dataclass + `app_lifespan` async context manager create a shared `httpx.AsyncClient` reused across all tool calls. Access it via `_get_client(ctx)`.
- **API layer**: `api_request()` is the single point for all HTTP calls with unified error handling (auth, rate limit, timeout, connection errors). Returns parsed JSON on success or an error string.
- **Two API versions**: v3 (`/api/rest/v3/`) for clusters, opinions, and citation lookup; v4 (`/api/rest/v4/search/`) for keyword and semantic search.
- **Result formatting**: `format_search_results()` converts v4 search responses to readable text. `_search_params()` builds query parameters with shared filtering logic.

## Conventions

- All tool functions are async, accept a `ctx: Context` parameter, and return `str`.
- Error handling uses string returns (not exceptions) — check `isinstance(data, str)` after `api_request()` calls.
- HTML is stripped from opinion text and search snippets using regex (`re.sub`).
- Results are capped at 1–20 via `_search_params()`.
- No test suite or linting configuration exists yet.

# CourtListener MCP Server

An MCP server that provides legal research tools powered by the [CourtListener](https://www.courtlistener.com/) case law database. Search cases by keyword, citation, or natural language, retrieve full opinion text, and find citing cases.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package runner)
- A free CourtListener API token — get one at https://www.courtlistener.com/help/api/rest/#permissions

## Setup

### 1. Place the server files

Copy this folder somewhere permanent on your machine, for example:

```bash
cp -r courtlistener-mcp ~/courtlistener-mcp
```

The folder must contain at minimum:
- `server.py` — the MCP server
- `pyproject.toml` — Python package definition

### 2. Add the server to Claude Code

Run this from anywhere, replacing `your_token_here` with your CourtListener API token and adjusting the path to where you placed the folder:

```bash
claude mcp add courtlistener \
  -e COURTLISTENER_API_TOKEN=your_token_here \
  -- uv run --directory ~/courtlistener-mcp courtlistener-mcp
```

That's it. Claude Code will install dependencies and run the server automatically via `uv`.

### Verify it works

```bash
claude mcp list
```

You should see `courtlistener` listed. Start a new Claude Code session and the legal research tools will be available.

## Tools

| Tool | Description |
|------|-------------|
| `search_cases` | Keyword search for case law opinions |
| `semantic_search` | Natural language search for conceptually similar cases |
| `lookup_citation` | Resolve a legal citation (e.g. "410 U.S. 113") to a case |
| `get_case_text` | Retrieve the full text of a court opinion |
| `find_citing_cases` | Find cases that cite a given decision |

## Uninstall

```bash
claude mcp remove courtlistener
```

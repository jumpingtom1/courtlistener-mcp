"""CourtListener MCP Server — search case law by citation, keyword, or semantic text."""

import os
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from mcp.server.fastmcp import FastMCP, Context

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_TOKEN = os.environ.get("COURTLISTENER_API_TOKEN", "")
BASE_V3 = "https://www.courtlistener.com/api/rest/v3"
BASE_V4 = "https://www.courtlistener.com/api/rest/v4"

# ---------------------------------------------------------------------------
# Lifespan — shared httpx client
# ---------------------------------------------------------------------------


@dataclass
class AppContext:
    client: httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(app: FastMCP) -> AsyncIterator[AppContext]:
    headers = {"Authorization": f"Token {API_TOKEN}"} if API_TOKEN else {}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    ) as client:
        yield AppContext(client=client)


mcp = FastMCP(
    "courtlistener",
    instructions=(
        "Legal research server providing access to the CourtListener case law database. "
        "Use search_cases for keyword searches, semantic_search for natural language queries, "
        "lookup_citation for resolving citations, get_case_text for full opinion text, "
        "and find_citing_cases to discover cases citing a given decision."
    ),
    lifespan=app_lifespan,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client(ctx: Context) -> httpx.AsyncClient:
    return ctx.request_context.lifespan_context.client


async def api_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> dict | list | str:
    """Make an API request with standardised error handling.

    Returns parsed JSON (dict or list) on success, or an error string.
    """
    if not API_TOKEN:
        return "Error: COURTLISTENER_API_TOKEN environment variable is not set."
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:
            return "Error: Invalid API token. Check COURTLISTENER_API_TOKEN."
        if code == 429:
            return "Error: Rate limit exceeded. CourtListener allows 5,000 requests/day."
        if code == 404:
            return "Error: Resource not found."
        return f"Error: HTTP {code} — {exc.response.text[:300]}"
    except httpx.TimeoutException:
        return "Error: Request timed out after 30 seconds."
    except httpx.RequestError as exc:
        return f"Error: Connection failed — {exc}"


def format_search_results(data: dict, header: str) -> str:
    """Format a v4 search response into readable text."""
    count = data.get("count", 0)
    results = data.get("results", [])

    if not results:
        return f"No results found. {header}"

    lines: list[str] = [f"{header} ({count} total results)\n"]

    for i, case in enumerate(results, 1):
        cites = case.get("citations") or []
        if isinstance(cites, list):
            if cites and isinstance(cites[0], dict):
                cite_strs = [
                    f"{c.get('volume', '')} {c.get('reporter', '')} {c.get('page', '')}".strip()
                    for c in cites
                ]
            else:
                cite_strs = [str(c) for c in cites]
        else:
            cite_strs = [str(cites)]
        citation_text = ", ".join(cite_strs) or "None"

        snippet = case.get("snippet", "N/A")
        # Strip HTML highlight tags from snippet
        snippet = re.sub(r"</?mark>", "", snippet)

        lines.append(
            f"{i}. {case.get('caseName', 'Unknown')}"
            f" ({case.get('court', '?')}, {case.get('dateFiled', '?')})\n"
            f"   Citations: {citation_text}\n"
            f"   Cited by: {case.get('citeCount', case.get('citation_count', 0))} cases\n"
            f"   Snippet: {snippet}\n"
            f"   Cluster ID: {case.get('cluster_id', 'N/A')}\n"
            f"   URL: https://www.courtlistener.com{case.get('absolute_url', '')}\n"
        )

    return "\n".join(lines)


def _search_params(
    query: str,
    court: str,
    filed_after: str,
    filed_before: str,
    order_by: str,
    limit: int,
) -> dict:
    """Build query params for a v4 opinion search."""
    params: dict = {"type": "o", "q": query, "order_by": order_by}
    if court:
        params["court"] = court
    if filed_after:
        params["filed_after"] = filed_after
    if filed_before:
        params["filed_before"] = filed_before
    params["limit"] = min(max(limit, 1), 20)
    return params


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_cases(
    query: str,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    order_by: str = "score desc",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Search CourtListener for case law opinions by keywords.

    Args:
        query: Keywords to search for (e.g. "fourth amendment search seizure").
               Use quotes around phrases for exact matching.
        court: Court filter code (e.g. "scotus", "ca9", "or orctapp").
               Multiple courts separated by spaces.
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        order_by: Sort order. Options: "score desc" (relevance), "dateFiled desc"
                  (newest), "dateFiled asc" (oldest), "citeCount desc" (most cited).
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    params = _search_params(query, court, filed_after, filed_before, order_by, limit)
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return format_search_results(data, f'Search results for "{query}"')


@mcp.tool()
async def lookup_citation(
    citation: str,
    ctx: Context = None,
) -> str:
    """Look up a legal citation and resolve it to the corresponding case.

    Args:
        citation: A legal citation string (e.g. "410 U.S. 113", "576 US 644",
                  "347 U.S. 483"). Can also include surrounding text — the API
                  will extract and resolve all citations found.
    """
    client = _get_client(ctx)
    data = await api_request(
        client, "POST", f"{BASE_V3}/citation-lookup/", data={"text": citation}
    )
    if isinstance(data, str):
        return data

    if not data:
        return f"No cases found for citation: {citation}"

    lines: list[str] = []
    for item in data:
        cite_str = item.get("citation", "?")
        normalized = item.get("normalized_citations", [])
        status = item.get("status", "?")

        lines.append(f"Citation: {cite_str}")
        if normalized:
            lines.append(f"Normalized: {', '.join(normalized)}")
        lines.append(f"Status: {status}")

        clusters = item.get("clusters")
        if clusters and status == 200:
            # clusters may be a single object or list depending on version
            if isinstance(clusters, dict):
                clusters = [clusters]
            for cl in clusters:
                case_name = cl.get("case_name", "Unknown")
                date_filed = cl.get("date_filed", "?")
                cl_id = cl.get("id", "?")
                url = cl.get("absolute_url", "")
                cites = cl.get("citations", [])
                cite_text = ", ".join(
                    f"{c.get('volume', '')} {c.get('reporter', '')} {c.get('page', '')}".strip()
                    for c in cites
                ) if cites else "None"

                lines.append(f"\nCase: {case_name}")
                lines.append(f"Date Filed: {date_filed}")
                lines.append(f"Citations: {cite_text}")
                lines.append(f"Cluster ID: {cl_id}")
                if url:
                    lines.append(f"URL: https://www.courtlistener.com{url}")
        elif status == 404:
            lines.append("No matching case found for this citation.")

        lines.append("")

    return "\n".join(lines).strip()


@mcp.tool()
async def semantic_search(
    query: str,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Search for case law using natural language / semantic similarity.

    Unlike keyword search, this finds conceptually similar cases even when
    different terminology is used. Put specific required terms in quotation
    marks to force exact keyword matching within semantic results.

    Args:
        query: Natural language description of the legal concept
               (e.g. "when can police search a car without a warrant").
        court: Court filter code (e.g. "scotus", "ca9").
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    params = _search_params(query, court, filed_after, filed_before, "score desc", limit)
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return format_search_results(data, f'Semantic search results for "{query}"')


@mcp.tool()
async def get_case_text(
    cluster_id: int = 0,
    opinion_id: int = 0,
    max_characters: int = 50000,
    ctx: Context = None,
) -> str:
    """Retrieve the full text of a court opinion.

    Provide either a cluster_id (case-level ID from search results) or a
    specific opinion_id. If cluster_id is provided, fetches the primary
    opinion in the cluster.

    Args:
        cluster_id: The cluster ID of the case (from search results).
        opinion_id: The specific opinion ID (if known).
        max_characters: Maximum characters of opinion text to return (default 50000).
    """
    if cluster_id == 0 and opinion_id == 0:
        return "Error: Provide either cluster_id or opinion_id."

    client = _get_client(ctx)

    # Resolve cluster_id → opinion_id if needed
    case_name = "Unknown"
    date_filed = "?"
    court = "?"
    case_url = ""

    if opinion_id == 0:
        cluster_data = await api_request(
            client,
            "GET",
            f"{BASE_V3}/clusters/{cluster_id}/",
        )
        if isinstance(cluster_data, str):
            return cluster_data

        case_name = cluster_data.get("case_name", "Unknown")
        date_filed = cluster_data.get("date_filed", "?")
        case_url = cluster_data.get("absolute_url", "")

        sub_opinions = cluster_data.get("sub_opinions", [])
        if not sub_opinions:
            return f"Error: No opinions found in cluster {cluster_id}."

        # Extract opinion ID from URL: ".../opinions/12345/"
        first_url = sub_opinions[0] if isinstance(sub_opinions[0], str) else ""
        match = re.search(r"/opinions/(\d+)/", first_url)
        if match:
            opinion_id = int(match.group(1))
        else:
            return f"Error: Could not parse opinion ID from cluster data."

    # Fetch the opinion
    opinion_data = await api_request(
        client,
        "GET",
        f"{BASE_V3}/opinions/{opinion_id}/",
    )
    if isinstance(opinion_data, str):
        return opinion_data

    # Extract text (priority: plain_text > html variants)
    text = opinion_data.get("plain_text", "")
    source = "plain_text"

    if not text:
        for field in [
            "html_with_citations",
            "html",
            "html_columbia",
            "html_lawbox",
            "html_anon_2020",
            "xml_harvard",
        ]:
            raw = opinion_data.get(field, "")
            if raw:
                text = re.sub(r"<[^>]+>", "", raw)
                source = field
                break

    if not text:
        return f"Error: No text content available for opinion {opinion_id}."

    # Get metadata from opinion if we didn't fetch cluster
    author = opinion_data.get("author_str", "")
    op_type = opinion_data.get("type", "")

    # Truncate if needed
    truncated = False
    if len(text) > max_characters:
        text = text[:max_characters]
        truncated = True

    lines = []
    if case_name != "Unknown":
        lines.append(f"Case: {case_name}")
    if date_filed != "?":
        lines.append(f"Date: {date_filed}")
    if author:
        lines.append(f"Author: {author}")
    if op_type:
        lines.append(f"Opinion Type: {op_type}")
    lines.append(f"Text Source: {source}")
    lines.append(f"Opinion ID: {opinion_id}")
    lines.append("")
    lines.append("--- OPINION TEXT ---")
    lines.append(text)

    if truncated:
        full_url = f"https://www.courtlistener.com{case_url}" if case_url else ""
        lines.append(
            f"\n[Truncated at {max_characters:,} characters. "
            f"Full opinion: {full_url}]"
        )

    return "\n".join(lines)


@mcp.tool()
async def find_citing_cases(
    cluster_id: int,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    order_by: str = "score desc",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Find cases that cite a given case.

    Args:
        cluster_id: Cluster ID of the case to find citations for
                    (obtain from search results or lookup_citation).
        court: Court filter code (e.g. "scotus", "ca9").
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        order_by: Sort order (default: "score desc").
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    query = f"cites:({cluster_id})"
    params = _search_params(query, court, filed_after, filed_before, order_by, limit)
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return format_search_results(data, f"Cases citing cluster {cluster_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

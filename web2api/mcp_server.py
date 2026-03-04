"""MCP protocol server — exposes all web2api recipes as MCP tools.

Uses the official MCP Python SDK with Streamable HTTP transport,
mounted onto the existing FastAPI application at /mcp.

Clients connect via:
    claude mcp add --transport http web2api https://web2api.endogen.dev/mcp
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger(__name__)

TOOL_NAME_SEP = "__"


def _build_tool_name(slug: str, endpoint: str) -> str:
    return f"{slug}{TOOL_NAME_SEP}{endpoint}"


def _parse_tool_name(name: str) -> tuple[str, str] | None:
    parts = name.split(TOOL_NAME_SEP, 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def create_mcp_server(web2api_internal_url: str = "http://127.0.0.1:8000") -> FastMCP:
    """Create a FastMCP server that dynamically discovers and proxies web2api recipes."""

    mcp = FastMCP(
        "Web2API",
        instructions=(
            "Web2API turns websites into REST APIs via live browser scraping. "
            "Each tool corresponds to a recipe endpoint. Use the tool name pattern "
            "{slug}__{endpoint} to call specific scrapers."
        ),
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    # We'll register tools dynamically at startup by querying the internal API.
    # Since FastMCP needs tools registered before serving, we use a lazy
    # approach: register a generic "call" tool and a "list_recipes" tool,
    # plus dynamically register all discovered recipe tools.

    @mcp.tool()
    async def list_recipes() -> str:
        """List all available web2api recipes and their endpoints."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{web2api_internal_url}/api/sites")
            resp.raise_for_status()
            sites = resp.json()

        lines = []
        for site in sites:
            slug = site.get("slug", "?")
            name = site.get("name", slug)
            desc = site.get("description", "")
            lines.append(f"\n## {name} ({slug})")
            if desc:
                lines.append(f"{desc}")
            for ep in site.get("endpoints", []):
                ep_name = ep.get("name", "?")
                ep_desc = ep.get("description", "")
                tool_name = _build_tool_name(slug, ep_name)
                params = ep.get("params", {})
                param_list = ", ".join(params.keys()) if params else ""
                requires_q = ep.get("requires_query", False)
                q_note = " (requires q)" if requires_q else ""
                lines.append(f"  - **{tool_name}**: {ep_desc}{q_note}")
                if param_list:
                    lines.append(f"    Extra params: {param_list}")

        return "\n".join(lines) if lines else "No recipes installed."

    @mcp.tool()
    async def call_recipe(
        tool_name: str,
        q: str | None = None,
        extra_params: str | None = None,
    ) -> str:
        """Call any web2api recipe endpoint.

        Args:
            tool_name: The tool name in {slug}__{endpoint} format (e.g. brave-search__search, deepl__de-en)
            q: The query string (required for endpoints marked with 'q')
            extra_params: Optional JSON object of additional parameters (e.g. '{"count": "5"}')
        """
        parsed = _parse_tool_name(tool_name)
        if not parsed:
            return f"Error: Invalid tool name '{tool_name}'. Use format: slug__endpoint"

        slug, endpoint = parsed
        url = f"{web2api_internal_url}/{slug}/{endpoint}"

        params: dict[str, str] = {"page": "1"}
        if q:
            params["q"] = q
        if extra_params:
            try:
                extra = json.loads(extra_params)
                if isinstance(extra, dict):
                    params.update({k: str(v) for k, v in extra.items()})
            except json.JSONDecodeError:
                return f"Error: extra_params must be valid JSON"

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                return f"Error: HTTP {e.response.status_code} — {e.response.text[:500]}"
            except httpx.RequestError as e:
                return f"Error: {e}"

        data = resp.json()
        error = data.get("error")
        if error:
            return f"Error: {error.get('message', 'unknown error')}"

        items = data.get("items", [])
        if not items:
            return "No results found."

        # Format results nicely
        results = []
        for item in items:
            fields = item.get("fields", {})
            title = item.get("title", "")
            url_field = item.get("url", "")

            # For single-field responses (like AI chat), return the main field
            for key in ("response", "answer", "text", "content", "result"):
                if key in fields:
                    if len(items) == 1:
                        return fields[key]
                    results.append(fields[key])
                    break
            else:
                # Multi-field item
                parts = []
                if title:
                    parts.append(f"**{title}**")
                if url_field:
                    parts.append(url_field)
                for k, v in fields.items():
                    parts.append(f"{k}: {v}")
                results.append("\n".join(parts))

        return "\n\n---\n\n".join(results)

    return mcp


def mount_mcp_server(app: Any) -> None:
    """Mount the MCP server onto a FastAPI/Starlette app at /mcp.

    Uses stateless mode to avoid needing session management lifecycle.
    """
    mcp = create_mcp_server()

    mcp_app = mcp.streamable_http_app()

    # Hook into FastAPI's startup/shutdown to manage the MCP session manager
    from contextlib import asynccontextmanager

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(a):
        async with mcp.session_manager.run():
            if original_lifespan:
                async with original_lifespan(a) as state:
                    yield state
            else:
                yield

    app.router.lifespan_context = combined_lifespan
    app.mount("/mcp", mcp_app)
    logger.info("MCP protocol server mounted at /mcp")

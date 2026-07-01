"""MCP Server context for external MCP server integration.

This module provides MCPServerContext, which enables easy integration
of external MCP (Model Context Protocol) servers.

Example:
    from shepherd_contexts.mcp import MCPServerContext

    # Simple stdio server
    fs = MCPServerContext(
        name="filesystem",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/projects"),
    )

    # From config file
    servers = MCPServerContext.from_yaml("mcp_servers.yaml")
"""

from shepherd_contexts.mcp.effects import MCPServerConnected, MCPToolCalled
from shepherd_contexts.mcp.server import MCPServerContext

__all__ = [
    "MCPServerConnected",
    "MCPServerContext",
    "MCPToolCalled",
]

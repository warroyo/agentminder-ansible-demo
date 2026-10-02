"""Tiny MCP server for the AgentMinder demo.

Three tools, split across two intents by the Ansible config:
  get_time, list_services  -> amdemo.read
  restart_service          -> amdemo.write
The server itself does no auth. AgentMinder's gateway sits in front of it and
decides which agent may call which tool.
"""

import datetime
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "agentminder-demo-mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
    json_response=True,
)

SERVICES = {"web": "running", "db": "running", "cache": "degraded"}


@mcp.tool()
def get_time() -> str:
    """Return the current UTC time."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@mcp.tool()
def list_services() -> dict:
    """List demo services and their status."""
    return SERVICES


@mcp.tool()
def restart_service(name: str) -> str:
    """Restart a demo service. This is the 'dangerous' write tool."""
    if name not in SERVICES:
        return "unknown service %s" % name
    SERVICES[name] = "running"
    print("RESTARTED %s" % name, flush=True)
    return "restarted %s" % name


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

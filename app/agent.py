"""Scripted demo agent for AgentMinder.

The agent holds no AgentMinder credentials. It talks MCP (JSON-RPC over HTTP)
to the AgentMinder sidecar in the same pod, which gets the token and forwards
each call to the AgentMinder gateway.

Every INTERVAL seconds it lists the tools it may use, calls each tool in
CALLS, and prints whether the call was allowed or denied.

Standard library only, so the pod needs no pip install.
"""

import json
import os
import time
import urllib.error
import urllib.request

SIDECAR_URL = os.environ.get("SIDECAR_URL", "http://127.0.0.1:8181").rstrip("/")
SIDECAR_TOKEN = os.environ["SIDECAR_TOKEN"]
CALLS = json.loads(os.environ.get("CALLS", "[]"))  # [{"tool": "...", "args": {...}}]
INTERVAL = int(os.environ.get("INTERVAL", "60"))


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


def rpc(method, params=None):
    """One MCP request through the sidecar. Returns (result, error)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(SIDECAR_URL + "/mcp", data=body, headers={
        "Authorization": "Bearer " + SIDECAR_TOKEN,
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            reply = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return None, "HTTP %s %s" % (e.code, e.read().decode(errors="replace")[:200])
    if "error" in reply:
        return None, reply["error"].get("message", str(reply["error"]))
    return reply.get("result"), None


def text_of(result):
    """Tool output as text, whichever shape the sidecar returns."""
    if isinstance(result, dict) and result.get("content"):
        return result["content"][0].get("text")
    return result if isinstance(result, str) else json.dumps(result)


def run_once():
    result, error = rpc("tools/list")
    if error:
        log("tools/list failed: %s" % error)
        return
    log("tools/list -> %s" % [t["name"] for t in result["tools"]])
    for call in CALLS:
        result, error = rpc("tools/call", {"name": call["tool"],
                                           "arguments": call.get("args", {})})
        if error:
            log("DENIED   %-16s -> %s" % (call["tool"], error[:200]))
        else:
            log("ALLOWED  %-16s -> %s" % (call["tool"], text_of(result)))


def wait_for_sidecar():
    while True:
        try:
            urllib.request.urlopen(SIDECAR_URL + "/healthz", timeout=5)
            return
        except OSError:
            time.sleep(2)


def main():
    log("agent starting, sidecar %s" % SIDECAR_URL)
    wait_for_sidecar()
    while True:
        try:
            run_once()
        except Exception as e:  # keep the demo loop alive
            log("error: %s" % e)
        if INTERVAL <= 0:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

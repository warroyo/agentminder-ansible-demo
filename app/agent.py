"""Scripted demo agent for AgentMinder.

Every INTERVAL seconds it:
  1. gets a client_credentials token from AgentMinder, asking for INTENTS
  2. talks MCP (JSON-RPC over streamable HTTP) to the AgentMinder gateway route
  3. calls each tool in CALLS and prints whether the gateway allowed or denied it

Standard library only, so the pod needs no pip install.
"""

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AM_URL = os.environ["AM_URL"].rstrip("/")          # https://host/<tenant>
GATEWAY_URL = os.environ["GATEWAY_URL"]            # .../aigateway/v1/mcp/<route>
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
INTENTS = os.environ.get("INTENTS", "").split()
CALLS = json.loads(os.environ.get("CALLS", "[]"))  # [{"tool": "...", "args": {...}}]
INTERVAL = int(os.environ.get("INTERVAL", "60"))
# The AgentMinder gateway rejects older MCP protocol versions.
PROTOCOL_VERSION = "2025-06-18"

if os.environ.get("VERIFY_TLS", "true").lower() == "false":
    ctx = ssl._create_unverified_context()
else:
    ctx = ssl.create_default_context(cafile=os.environ.get("CA_FILE") or None)
    # Lets a bare self-signed AgentMinder cert act as its own trust anchor.
    ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


def http(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


def get_token():
    basic = base64.b64encode(("%s:%s" % (
        urllib.parse.quote(CLIENT_ID, safe=""),
        urllib.parse.quote(CLIENT_SECRET, safe=""))).encode()).decode()
    # resource (RFC 8707) names the MCP server's gateway route. Without it
    # AgentMinder rejects intent scopes as "Invalid scope". Intents the policy
    # does not grant are silently dropped from the issued token.
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "resource": GATEWAY_URL,
        "scope": " ".join(INTENTS)}).encode()
    status, _, text = http(AM_URL + "/oauth2/v1/token", body, {
        "Authorization": "Basic " + basic,
        "Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise RuntimeError("token request failed: %s %s" % (status, text))
    tok = json.loads(text)
    return tok["access_token"], tok.get("scope", "")


class Mcp(object):
    def __init__(self, token):
        self.token = token
        self.session = None
        self.next_id = 0

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        status, rh, text = http(GATEWAY_URL, json.dumps(msg).encode(), headers)
        sid = {k.lower(): v for k, v in rh.items()}.get("mcp-session-id")
        if sid:
            self.session = sid
        if notify or status == 202:
            return status, None
        if text.lstrip().startswith(("event:", "data:")):
            # SSE framing: take the last data: line.
            data = [l[5:].strip() for l in text.splitlines() if l.startswith("data:")]
            text = data[-1] if data else ""
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text


def run_once():
    token, granted = get_token()
    log("token issued, granted scopes: %s" % granted)
    m = Mcp(token)
    status, res = m.rpc("initialize", {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "agentminder-demo-agent", "version": "0.1"}})
    # The gateway reports an unsupported protocol version as HTTP 200 with a
    # JSON-RPC error, so check the body too.
    if status != 200 or not isinstance(res, dict) or "result" not in res:
        log("initialize failed -> HTTP %s %s" % (status, res))
        return
    m.rpc("notifications/initialized", notify=True)
    status, res = m.rpc("tools/list", {})
    tools = [t["name"] for t in (res or {}).get("result", {}).get("tools", [])] \
        if isinstance(res, dict) else res
    log("tools/list -> HTTP %s %s" % (status, tools))
    for call in CALLS:
        status, res = m.rpc("tools/call", {"name": call["tool"],
                                           "arguments": call.get("args", {})})
        if status == 200 and isinstance(res, dict) and "result" in res:
            content = res["result"].get("content") or [{}]
            log("ALLOWED  %-16s -> %s" % (call["tool"], content[0].get("text")))
        else:
            detail = res.get("error", res) if isinstance(res, dict) else res
            log("DENIED   %-16s -> HTTP %s %s" % (call["tool"], status, str(detail)[:200]))


def main():
    log("agent %s starting, gateway %s" % (CLIENT_ID, GATEWAY_URL))
    while True:
        try:
            run_once()
        except Exception as e:  # keep the demo loop alive
            log("error: %s" % e)
        if INTERVAL <= 0:
            return
        sys.stdout.flush()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

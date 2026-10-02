# -*- coding: utf-8 -*-
"""Shared AgentMinder admin API client for the modules in this repo.

Authenticates with OAuth client_credentials against the tenant token endpoint
and talks to the tenant-prefixed admin API (https://<host>/<tenant>/admin/v1/...).
Only the standard library is used so the modules run on any controller.
"""

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

ADMIN_SCOPES = (
    "urn:iam:t.apps urn:iam:t.authzpolicies urn:iam:t.aigateways "
    "urn:iam:t.airesources urn:iam:t.airesourceservers"
)

INTENT_PREFIX = "urn:iam:agent:intent:"


def connection_argument_spec():
    return dict(
        base_url=dict(type="str", required=True),
        tenant=dict(type="str", default="default"),
        client_id=dict(type="str", required=True, no_log=True),
        client_secret=dict(type="str", required=True, no_log=True),
        ca_cert=dict(type="str", required=False),
        validate_certs=dict(type="bool", default=True),
    )


def intent_scope(name):
    return name if name.startswith("urn:") else INTENT_PREFIX + name


class AgentMinderError(Exception):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg)
        self.status = status
        self.body = body


class AgentMinderClient(object):
    def __init__(self, params):
        self.base = params["base_url"].rstrip("/") + "/" + params["tenant"]
        self.ctx = self._ssl_context(params)
        self.token = self._get_token(params["client_id"], params["client_secret"])

    @staticmethod
    def _ssl_context(params):
        if not params.get("validate_certs", True):
            return ssl._create_unverified_context()
        if params.get("ca_cert"):
            ctx = ssl.create_default_context(cadata=params["ca_cert"])
        else:
            ctx = ssl.create_default_context()
        # The AgentMinder CA is a bare self-signed cert; accept it as a trust anchor.
        ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        return ctx

    def _open(self, req):
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode(errors="replace")
            raise AgentMinderError(
                "%s %s -> HTTP %s: %s" % (req.get_method(), req.full_url, e.code, body),
                status=e.code, body=body)

    def _get_token(self, client_id, client_secret):
        basic = base64.b64encode(
            (urllib.parse.quote(client_id, safe="") + ":" +
             urllib.parse.quote(client_secret, safe="")).encode()).decode()
        req = urllib.request.Request(
            self.base + "/oauth2/v1/token",
            data=urllib.parse.urlencode(
                {"grant_type": "client_credentials", "scope": ADMIN_SCOPES}).encode(),
            headers={"Authorization": "Basic " + basic,
                     "Content-Type": "application/x-www-form-urlencoded"})
        _, body = self._open(req)
        return body["access_token"]

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/json",
                     "Content-Type": "application/json"})
        return self._open(req)[1]

    # --- lookups -----------------------------------------------------------

    def list_apps(self):
        return self.request("GET", "/admin/v1/Apps") or []

    def find_app(self, name):
        matches = [a for a in self.list_apps() if a.get("name") == name]
        if len(matches) > 1:
            raise AgentMinderError("more than one app named %s" % name)
        return matches[0] if matches else None

    def get_app(self, app_id):
        return self.request("GET", "/admin/v1/Apps/%s?resolveMetadataObjIds=true" % app_id)

    def gateway_route_url(self, app_id):
        route = base64.b64encode(app_id.encode()).decode().rstrip("=")
        return "%s/aigateway/v1/mcp/%s" % (self.base, route)

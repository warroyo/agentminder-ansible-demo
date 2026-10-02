#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = r"""
module: agentminder_app
short_description: Manage an AgentMinder agent app, orchestrator client or MCP resource server
description:
  - C(type=agent) registers an AI agent: an OAuth confidential client that gets
    tokens with C(client_credentials).
  - C(type=orchestrator) registers a plain confidential client for the SDK or
    sidecar control-plane calls (agent profile, missions). It gets its rights
    from an authorization policy that grants
    C(urn:iam:t.aiagentorchestrationclient), see M(agentminder_policy).
  - C(type=mcp_server) registers an AI resource server for one MCP backend. It
    carries the backend URL, the gateway enforcement flags, the intents the
    server exposes and the tool-to-intent bindings. AgentMinder generates a
    gateway route for it at C(/<tenant>/aigateway/v1/mcp/<base64 appId>).
  - Only the fields listed here are managed. Everything else on the app is left
    as the server set it.
options:
  name: {type: str, required: true}
  type: {type: str, required: true, choices: [agent, orchestrator, mcp_server]}
  description: {type: str, default: ""}
  state: {type: str, default: present, choices: [present, absent]}
  risk_level:
    description: Agent risk label (C(agentRiskLevel)).
    type: str
    default: standard
  delegation_mode:
    description: C(AUTONOMOUS) agents act as themselves; delegated agents act inside a mission.
    type: str
    default: AUTONOMOUS
  use_all_allowed_intents:
    description: Issue every intent a policy allows even when the token request names fewer.
    type: bool
    default: false
  mcp_endpoint:
    description: Backend MCP URL the gateway forwards to.
    type: str
  primary_audience:
    description: Token audience. Defaults to the generated gateway route URL.
    type: str
  enforce_policies: {type: bool, default: true}
  protected_by_default:
    description: Tools with no binding cannot be called.
    type: bool
    default: true
  require_access_token: {type: bool, default: true}
  discover_require_access_token: {type: bool, default: true}
  require_intent_token: {type: bool, default: false}
  require_mission_liveness: {type: bool, default: false}
  require_dpop: {type: bool, default: false}
  ignore_backend_ssl: {type: bool, default: true}
  intents:
    description: Intents this resource server exposes.
    type: list
    elements: dict
    suboptions:
      name: {type: str, required: true}
      risk: {type: str, default: normal}
  tool_bindings:
    description: Map each MCP tool to one of the intents.
    type: list
    elements: dict
    suboptions:
      tool: {type: str, required: true}
      intent: {type: str, required: true}
      description: {type: str, default: ""}
  return_secret:
    description: Return the OAuth client secret. Use C(no_log) on the task.
    type: bool
    default: false
"""

RETURN = r"""
app_id: {description: Internal app ID (used in policies), type: str, returned: when present}
client_id: {description: OAuth client ID, type: str, returned: when present}
client_secret: {description: OAuth client secret, type: str, returned: when return_secret}
gateway_url: {description: Gateway MCP URL agents connect to, type: str, returned: type mcp_server}
"""

import copy

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.agentminder import (
    AgentMinderClient, AgentMinderError, connection_argument_spec, intent_scope)

READ_ONLY = ("secret", "createdBy", "updatedBy", "createdDateTime", "updatedDateTime")


def base_payload(p):
    payload = {
        "name": p["name"],
        "description": p["description"],
        "status": "active",
        "redirectURIs": ["https://www.example.com"],
        "allowedOpenIDScopes": ["openid"],
        "clientType": "CONFIDENTIAL",
        "itEncryptionTarget": "NONE",
        "userInfoEndpointResponseFormat": "JWT",
        "supportedJoseHeaderParams": "x5t,x5t#s256",
        "skewTimeSecs": 0,
        "passwordAuthoritativeSource": "remote",
        "delegatedAuthentication": False,
        "autoPostToFlowURL": False,
        "requireAccessControl": "false",
        "isOidcApp": True,
        "isSamlApp": False,
        "isLauncherApp": False,
        "hidden": False,
    }
    if p["type"] == "agent":
        payload.update({
            "allowedGrantTypes": ["client_credentials"],
            "isAgentApp": True,
            "isResourceServerApp": False,
            "isAiResourceServerApp": False,
            "agentMaxDelegationDepth": 0,
            "agentMissionCredentialType": None,
        })
    elif p["type"] == "orchestrator":
        payload.update({
            "allowedGrantTypes": ["client_credentials"],
            "allowedOpenIDScopes": [],
            "allowedOperations": ["introspect"],
            "isAgentApp": False,
            "isResourceServerApp": False,
            "isAiResourceServerApp": False,
        })
    else:
        payload.update({
            "allowedGrantTypes": ["client_credentials"],
            "isAgentApp": False,
            "isResourceServerApp": True,
            "isAiResourceServerApp": True,
            # Real audience needs the app ID; fixed up on the update pass.
            "primaryAudience": p["primary_audience"] or p["mcp_endpoint"],
        })
    payload.update(managed_fields(p, None))
    return payload


def managed_fields(p, app_id, client=None):
    f = {"description": p["description"], "status": "active"}
    if p["type"] == "agent":
        f.update({
            "agentRiskLevel": p["risk_level"],
            "agentDelegationMode": p["delegation_mode"],
            "agentUseAllAllowedIntents": p["use_all_allowed_intents"],
        })
        return f
    if p["type"] == "orchestrator":
        return f
    audience = p["primary_audience"]
    if not audience:
        audience = client.gateway_route_url(app_id) if app_id else p["mcp_endpoint"]
    f.update({
        "mcpServerEndpoint": p["mcp_endpoint"],
        "primaryAudience": audience,
        "enforcePolicies": p["enforce_policies"],
        "protectedByDefault": p["protected_by_default"],
        "mcpRequireAccessToken": p["require_access_token"],
        "mcpDiscoverRequireAccessToken": p["discover_require_access_token"],
        "mcpRequireIntentToken": p["require_intent_token"],
        "mcpRequireMissionLiveness": p["require_mission_liveness"],
        "mcpRequireDPoP": p["require_dpop"],
        "mcpIgnoreSslValidation": p["ignore_backend_ssl"],
        "resourceServerScopes": [
            {"scope": intent_scope(i["name"]), "risk": i.get("risk") or "normal",
             "consentRequired": False}
            for i in (p["intents"] or [])],
        "agentToolBindings": [
            {"toolName": b["tool"], "intentScope": intent_scope(b["intent"]),
             "description": b.get("description") or "", "skill": None, "source": "MANUAL"}
            for b in (p["tool_bindings"] or [])],
    })
    return f


def normalize(field, value):
    """Comparable form of a managed field; ignores server-added keys and order."""
    if field == "resourceServerScopes":
        return sorted((s.get("scope"), s.get("risk") or "normal") for s in (value or []))
    if field == "agentToolBindings":
        return sorted((b.get("toolName"), b.get("intentScope")) for b in (value or []))
    return value


def diff(current, desired):
    return sorted(k for k, v in desired.items()
                  if normalize(k, current.get(k)) != normalize(k, v))


def main():
    spec = connection_argument_spec()
    spec.update(
        name=dict(type="str", required=True),
        type=dict(type="str", required=True, choices=["agent", "orchestrator", "mcp_server"]),
        description=dict(type="str", default=""),
        state=dict(type="str", default="present", choices=["present", "absent"]),
        risk_level=dict(type="str", default="standard"),
        delegation_mode=dict(type="str", default="AUTONOMOUS"),
        use_all_allowed_intents=dict(type="bool", default=False),
        mcp_endpoint=dict(type="str"),
        primary_audience=dict(type="str"),
        enforce_policies=dict(type="bool", default=True),
        protected_by_default=dict(type="bool", default=True),
        require_access_token=dict(type="bool", default=True),
        discover_require_access_token=dict(type="bool", default=True),
        require_intent_token=dict(type="bool", default=False),
        require_mission_liveness=dict(type="bool", default=False),
        require_dpop=dict(type="bool", default=False),
        ignore_backend_ssl=dict(type="bool", default=True),
        intents=dict(type="list", elements="dict", default=[]),
        tool_bindings=dict(type="list", elements="dict", default=[]),
        return_secret=dict(type="bool", default=False),
    )
    module = AnsibleModule(
        argument_spec=spec, supports_check_mode=True,
        required_if=[("type", "mcp_server", ["mcp_endpoint"])])
    p = module.params
    result = dict(changed=False)

    try:
        client = AgentMinderClient(p)
        current = client.find_app(p["name"])

        if p["state"] == "absent":
            if current:
                result["changed"] = True
                if not module.check_mode:
                    client.request("DELETE", "/admin/v1/Apps/%s?force=true" % current["appId"])
            module.exit_json(**result)

        if current is None:
            result["changed"] = True
            if module.check_mode:
                module.exit_json(**result)
            created = client.request("POST", "/admin/v1/Apps", base_payload(p))
            app_id = (created or {}).get("appId") or client.find_app(p["name"])["appId"]
            current = client.get_app(app_id)
        else:
            current = client.get_app(current["appId"])

        app_id = current["appId"]
        desired = managed_fields(p, app_id, client)
        changes = diff(current, desired)
        if changes:
            result["changed"] = True
            result["diff_fields"] = changes
            if not module.check_mode:
                body = copy.deepcopy(current)
                for k in READ_ONLY:
                    body.pop(k, None)
                body.update(desired)
                client.request("PUT", "/admin/v1/Apps/%s?resolveMetadataObjIds=true" % app_id, body)
                current = client.get_app(app_id)
                still = diff(current, desired)
                if still:
                    module.fail_json(msg="fields did not converge after PUT: %s" % still, **result)

        result["app_id"] = app_id
        result["client_id"] = current.get("clientId")
        if p["type"] == "mcp_server":
            result["gateway_url"] = client.gateway_route_url(app_id)
        if p["return_secret"]:
            result["client_secret"] = current.get("secret")
    except AgentMinderError as e:
        module.fail_json(msg=str(e), status=e.status, body=e.body, **result)

    module.exit_json(**result)


if __name__ == "__main__":
    main()

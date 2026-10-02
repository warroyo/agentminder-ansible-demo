#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = r"""
module: agentminder_token
short_description: Check which scopes AgentMinder grants a client
description:
  - Requests a C(client_credentials) token for C(client_id) and returns the
    scopes AgentMinder granted. The token itself is not returned.
  - Changes nothing. Use it with C(until) to wait for a policy change to take
    effect, since new policies reach the token endpoint after a short delay.
options:
  scope:
    description: Scopes to ask for. C(urn:iam:myscopes) asks for every intent a policy grants.
    type: list
    elements: str
    default: []
  resource:
    description: Gateway route URL of the MCP resource server (RFC 8707). Needed for intent scopes.
    type: str
"""

EXAMPLES = r"""
- agentminder_token:
    client_id: "{{ agent_app.client_id }}"
    client_secret: "{{ agent_app.client_secret }}"
    scope: [urn:iam:myscopes]
    resource: "{{ mcp_app.gateway_url }}"
  register: token
  until: "'urn:iam:agent:intent:demo.read' in token.scopes"
  retries: 30
  delay: 2
"""

RETURN = r"""
scopes: {description: Granted scopes, type: list, elements: str, returned: always}
intents:
  description: Granted intents, without the C(urn:iam:agent:intent:) prefix.
  type: list
  elements: str
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.agentminder import (
    INTENT_PREFIX, AgentMinderClient, AgentMinderError, connection_argument_spec)


def main():
    spec = connection_argument_spec()
    spec.update(
        scope=dict(type="list", elements="str", default=[]),
        resource=dict(type="str"),
    )
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    p = module.params
    try:
        client = AgentMinderClient(p, authenticate=False)
        token = client.client_credentials(
            p["client_id"], p["client_secret"],
            scope=" ".join(p["scope"]), resource=p["resource"])
    except AgentMinderError as e:
        module.fail_json(msg=str(e), status=e.status, body=e.body)
    scopes = (token.get("scope") or "").split()
    module.exit_json(
        changed=False, scopes=scopes,
        intents=sorted(s[len(INTENT_PREFIX):] for s in scopes if s.startswith(INTENT_PREFIX)))


if __name__ == "__main__":
    main()

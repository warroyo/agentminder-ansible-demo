#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = r"""
module: agentminder_intent
short_description: Manage an entry in the AgentMinder intent catalog
description:
  - Creates, updates or removes an intent in the tenant intent catalog
    (C(/admin/v1/AgentIntentCatalog)). An intent grants nothing by itself; resource
    servers bind tools to it and authorization policies grant it to agents.
options:
  name:
    description: Short intent name without the C(urn:iam:agent:intent:) prefix.
    type: str
    required: true
  description:
    type: str
    default: ""
  risk:
    description: Risk label, for example C(normal) or C(elevated).
    type: str
    default: normal
  state:
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment: []
"""

EXAMPLES = r"""
- agentminder_intent:
    base_url: https://agentminder.example.com
    client_id: "{{ am_client_id }}"
    client_secret: "{{ am_client_secret }}"
    name: demo.clock.read
    description: Read the time
"""

RETURN = r"""
intent:
  description: The catalog entry after the change.
  type: dict
  returned: when state is present
scope:
  description: Full intent scope string.
  type: str
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.agentminder import (
    AgentMinderClient, AgentMinderError, connection_argument_spec, intent_scope)

PATH = "/admin/v1/AgentIntentCatalog"


def main():
    spec = connection_argument_spec()
    spec.update(
        name=dict(type="str", required=True),
        description=dict(type="str", default=""),
        risk=dict(type="str", default="normal"),
        state=dict(type="str", default="present", choices=["present", "absent"]),
    )
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    p = module.params
    result = dict(changed=False, scope=intent_scope(p["name"]))

    try:
        client = AgentMinderClient(p)
        catalog = client.request("GET", PATH) or []
        current = next((i for i in catalog if i.get("name") == p["name"]), None)

        if p["state"] == "absent":
            if current:
                result["changed"] = True
                if not module.check_mode:
                    client.request("DELETE", "%s/%s" % (PATH, current["agentIntentTypeId"]))
            module.exit_json(**result)

        desired = {"name": p["name"], "description": p["description"], "risk": p["risk"]}
        if current is None:
            result["changed"] = True
            if not module.check_mode:
                client.request("POST", PATH, desired)
        elif any(current.get(k) != v for k, v in desired.items()):
            result["changed"] = True
            if not module.check_mode:
                client.request("PUT", "%s/%s" % (PATH, current["agentIntentTypeId"]), desired)

        if not module.check_mode:
            catalog = client.request("GET", PATH) or []
            current = next((i for i in catalog if i.get("name") == p["name"]), None)
        result["intent"] = current or desired
    except AgentMinderError as e:
        module.fail_json(msg=str(e), status=e.status, body=e.body, **result)

    module.exit_json(**result)


if __name__ == "__main__":
    main()

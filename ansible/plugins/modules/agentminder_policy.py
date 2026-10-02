#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = r"""
module: agentminder_policy
short_description: Manage an AgentMinder authorization policy that grants intents to agents
description:
  - Manages one native C(role) policy (C(/admin/v1/AuthZPolicies)) with a single
    grant rule. The policy targets one MCP resource server and grants a list of
    intents to a list of agents.
  - Agents and the resource server are given by name and resolved to internal
    app IDs. Policies select agents by app ID, not OAuth client ID.
  - Rule IDs the server generates are ignored when comparing, so re-runs do not
    report drift.
options:
  name: {type: str, required: true}
  description: {type: str, default: ""}
  resource_server: {description: Name of the MCP resource server app, type: str}
  agents: {description: Agent app names, type: list, elements: str, default: []}
  intents: {description: Intent names or full scopes to grant, type: list, elements: str, default: []}
  active: {type: bool, default: true}
  state: {type: str, default: present, choices: [present, absent]}
"""

RETURN = r"""
policy_id: {type: str, returned: when present}
"""

import urllib.parse

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.agentminder import (
    AgentMinderClient, AgentMinderError, connection_argument_spec, intent_scope)

PATH = "/admin/v1/AuthZPolicies"


def semantic(policy):
    rules = []
    for r in policy.get("rules") or []:
        cond = (((r.get("conditions") or {}).get("principal") or {}).get("clientApp") or {})
        res = r.get("result") or {}
        rules.append((tuple(sorted(cond.get("value") or [])), res.get("effect"),
                      tuple(sorted(res.get("privileges") or []))))
    return {
        "description": policy.get("description") or "",
        "status": policy.get("status"),
        "matchAnyApp": policy.get("matchAnyApp"),
        "apps": sorted(a.get("id") for a in policy.get("apps") or []),
        "rules": sorted(rules),
    }


def main():
    spec = connection_argument_spec()
    spec.update(
        name=dict(type="str", required=True),
        description=dict(type="str", default=""),
        resource_server=dict(type="str"),
        agents=dict(type="list", elements="str", default=[]),
        intents=dict(type="list", elements="str", default=[]),
        active=dict(type="bool", default=True),
        state=dict(type="str", default="present", choices=["present", "absent"]),
    )
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True,
                           required_if=[("state", "present", ["resource_server"])])
    p = module.params
    result = dict(changed=False)

    try:
        client = AgentMinderClient(p)
        flt = urllib.parse.quote("(policyName eq %s)" % p["name"])
        found = client.request("GET", "%s?filter=%s" % (PATH, flt)) or []
        found = [x for x in found if x.get("policyName") == p["name"]]
        if len(found) > 1:
            module.fail_json(msg="more than one policy named %s" % p["name"])
        current = found[0] if found else None

        if p["state"] == "absent":
            if current:
                result["changed"] = True
                if not module.check_mode:
                    client.request("DELETE", "%s/%s" % (PATH, current["policyId"]))
            module.exit_json(**result)

        apps = {a["name"]: a for a in client.list_apps()}
        missing = [n for n in [p["resource_server"]] + p["agents"] if n not in apps]
        if missing:
            module.fail_json(msg="apps not found: %s" % ", ".join(missing))
        rs = apps[p["resource_server"]]

        desired = {
            "policyName": p["name"],
            "description": p["description"],
            "policySubType": "role",
            "status": "active" if p["active"] else "inactive",
            "matchAnyApp": False,
            "apps": [{"id": rs["appId"], "name": rs["name"]}],
            "rules": [{
                "conditions": {"principal": {"clientApp": {
                    "operator": "in",
                    "value": [apps[a]["appId"] for a in p["agents"]]}}},
                "result": {
                    "effect": "grant",
                    "msg": p["name"],
                    "privileges": [intent_scope(i) for i in p["intents"]]},
            }],
        }

        if current is None:
            result["changed"] = True
            if not module.check_mode:
                current = client.request("POST", PATH, desired)
        elif semantic(current) != semantic(desired):
            result["changed"] = True
            result["before"] = semantic(current)
            result["after"] = semantic(desired)
            if not module.check_mode:
                current = client.request("PUT", "%s/%s" % (PATH, current["policyId"]), desired)

        result["policy_id"] = (current or {}).get("policyId")
    except AgentMinderError as e:
        module.fail_json(msg=str(e), status=e.status, body=e.body, **result)

    module.exit_json(**result)


if __name__ == "__main__":
    main()

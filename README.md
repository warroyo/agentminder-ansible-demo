# AgentMinder agent + MCP demo, managed with Ansible

A quickstart that shows two things:

1. **AgentMinder governing an AI agent's MCP tool calls.** A policy decides
   which tools the agent can see and call, and you can flip it live.
2. **Managing AgentMinder with Ansible.** Three small custom modules create
   the intents, apps, tool bindings and policy, idempotently, from one
   playbook.

It assumes AgentMinder is already installed. Everything else runs on any
Kubernetes cluster: one `ansible-playbook` run deploys an MCP server,
registers it in AgentMinder, and starts an agent that calls tools through the
AgentMinder gateway.

No missions, no LLM. The agent is a scripted loop so the allow/deny behavior is
easy to see.

For how AgentMinder works underneath (object model, token and gateway flow,
admin API), see [docs/how-agentminder-works.md](docs/how-agentminder-works.md).

## Layout

```
ansible/
  site.yml, teardown.yml      the two playbooks
  tasks/                      MCP server, AgentMinder registration, agent
  templates/                  Kubernetes manifests
  plugins/modules/            agentminder_intent, agentminder_app, agentminder_policy
  plugins/module_utils/       shared admin API client
  inventory/group_vars/all/   defaults.yml (tracked), local.yml (yours, git-ignored)
app/                          agent.py, mcp_server.py
docs/                         how AgentMinder works, networking notes
```

## What gets built

```
demo cluster / agentminder-demo            AgentMinder
┌──────────────────────┐   token + MCP    ┌───────────────────────────────┐
│ amdemo-agent (pod)   │ ───────────────▶ │ token endpoint                │
│  get token (intents) │   HTTPS          │ aigateway ── PDP (policy)     │
│  tools/list          │                  │      │ allow                  │
│  tools/call x3       │                  └──────┼────────────────────────┘
│                      │                         │
│ mcp-server (pod)     │   HTTP                  │
│  LoadBalancer Service│ ◀───────────────────────┘
└──────────────────────┘
```

The agent never talks to the MCP server directly. It only knows the
AgentMinder token endpoint and the gateway route for the MCP server.

AgentMinder objects (all prefixed `amdemo`):

| Object | Name | Notes |
|---|---|---|
| Intent | `amdemo.read` | risk normal |
| Intent | `amdemo.write` | risk elevated |
| MCP resource server | `amdemo-mcp` | backend is the MCP server's URL, tool bindings below |
| Agent app | `amdemo-agent` | autonomous, confidential client |
| Policy | `amdemo-agent-can-read` | grants `granted_intents` to the agent on `amdemo-mcp` |

Tool bindings:

| Tool | Intent |
|---|---|
| `get_time` | `amdemo.read` |
| `list_services` | `amdemo.read` |
| `restart_service` | `amdemo.write` |

## Prerequisites

- AgentMinder, installed and reachable, plus an admin API client (client ID
  and secret) for the tenant.
- A Kubernetes cluster and a kubeconfig for it. Pods must be able to reach
  the AgentMinder URL.
- A way for AgentMinder to reach the MCP server on that cluster. The default
  is a `LoadBalancer` Service. See [MCP server exposure](#mcp-server-exposure).
- Python 3.10+ on the machine that runs Ansible.

## Quickstart

```bash
python3 -m venv .venv && . .venv/bin/activate
cd ansible
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml -p ./collections

cp inventory/group_vars/all/local.yml.example inventory/group_vars/all/local.yml
# edit local.yml: agentminder_url, admin client ID and secret, CA file

ansible-playbook site.yml
```

`local.yml` is git-ignored. The settings that matter:

| Variable | Default | Meaning |
|---|---|---|
| `agentminder_url` | none, required | Base URL, without the tenant |
| `agentminder_tenant` | `default` | Tenant path segment |
| `agentminder_client_id`, `agentminder_client_secret` | none, required | Admin API client |
| `agentminder_ca_file` | empty | PEM CA for the AgentMinder certificate. Leave empty for a public CA |
| `agentminder_validate_certs` | `true` | Set `false` to skip TLS verification (modules and agent) |
| `kubeconfig` | `$KUBECONFIG` or `~/.kube/config` | Demo cluster kubeconfig |
| `kube_context` | current context | Context in that kubeconfig |
| `mcp_expose` | `loadbalancer` | `loadbalancer`, `gateway` or `clusterip` |

All defaults are in
[`ansible/inventory/group_vars/all/defaults.yml`](ansible/inventory/group_vars/all/defaults.yml).

Tags: `mcp` (MCP server only), `agentminder` (plus registration), `agent`
(everything).

## Demo flow

1. Watch the agent:

   ```bash
   kubectl -n agentminder-demo logs deploy/amdemo-agent -f
   ```

   With the default policy the token only carries `amdemo.read`, `tools/list`
   only shows the two read tools, and the write call is refused by the gateway:

   ```
   token issued, granted scopes: urn:iam:agent:intent:amdemo.read urn:iam:m.meclient
   tools/list -> HTTP 200 ['get_time', 'list_services']
   ALLOWED  get_time         -> 2026-09-29T18:38:54.237291+00:00
   ALLOWED  list_services    -> {...}
   DENIED   restart_service  -> HTTP 403
   ```

2. Grant the write intent and re-run only the registration:

   ```bash
   ansible-playbook site.yml --tags agentminder \
     -e '{"granted_intents": ["amdemo.read", "amdemo.write"]}'
   ```

   On the next agent cycle (60s) the token carries `amdemo.write`,
   `restart_service` shows up in `tools/list`, and the call is ALLOWED.

3. Revoke it by re-running with the defaults.

4. Gateway audit trail, if you have access to the AgentMinder cluster
   (namespace and deployment names depend on your install):

   ```bash
   kubectl -n ssp logs deploy/ssp-ssp-aigateway -c ssp-aigateway --since=10m \
     | grep -E 'MCP: tool call|request denied'
   ```

   ```
   MCP: tool call "get_time" allowed on backend "amdemo-mcp"
   MCP: tool call "restart_service" denied on backend "amdemo-mcp": policy/authorize
   ```

   `authorize` is the gateway's own authorization step reporting "no policy
   granted this", not a policy you created.

## Tear down

```bash
ansible-playbook teardown.yml
```

## Ansible modules

Custom modules in `ansible/plugins/modules`, shared client in
`ansible/plugins/module_utils/agentminder.py` (standard library only). All
three are idempotent and support check mode.

| Module | API | Manages |
|---|---|---|
| `agentminder_intent` | `/admin/v1/AgentIntentCatalog` | intent catalog entries |
| `agentminder_app` | `/admin/v1/Apps` | agent apps and MCP resource servers, including `resourceServerScopes` and `agentToolBindings`. Returns `app_id`, `client_id`, `gateway_url`, and the client secret when `return_secret: true` |
| `agentminder_policy` | `/admin/v1/AuthZPolicies` | one `role` policy with one grant rule. App names are resolved to app IDs, and server-generated rule IDs are ignored when diffing |

Connection options shared by every module: `base_url`, `tenant`,
`client_id`, `client_secret`, `ca_cert`, `validate_certs`. `site.yml` sets
them once with `module_defaults`.

The whole registration is four tasks in
[`ansible/tasks/agentminder.yml`](ansible/tasks/agentminder.yml):

```yaml
- name: Intents in the tenant catalog
  agentminder_intent:
    name: "{{ item.name }}"
    description: "{{ item.description }}"
    risk: "{{ item.risk }}"
  loop: "{{ intents }}"

- name: MCP resource server with tool bindings
  agentminder_app:
    name: amdemo-mcp
    type: mcp_server
    mcp_endpoint: "{{ mcp_endpoint }}"
    intents: "{{ intents }}"
    tool_bindings: "{{ tool_bindings }}"

- name: Agent app
  agentminder_app:
    name: amdemo-agent
    type: agent
    return_secret: true
  no_log: true

- name: Policy granting intents to the agent
  agentminder_policy:
    name: amdemo-agent-can-read
    resource_server: amdemo-mcp
    agents: [amdemo-agent]
    intents: "{{ granted_intents }}"
```

## MCP server exposure

The AgentMinder gateway has to reach the MCP server. The agent needs no
Service; it only makes outbound calls. `mcp_expose` picks how the MCP server
is published:

| `mcp_expose` | What is created | Backend URL registered in AgentMinder |
|---|---|---|
| `loadbalancer` (default) | `Service` of type `LoadBalancer` | `http://<load balancer address>/mcp` |
| `gateway` | Gateway API `Gateway` + `HTTPRoute` using `mcp_gateway_class` | `http://<mcp_hostname>/mcp`. Without `mcp_hostname`, an `sslip.io` name is derived from the Gateway address |
| `clusterip` | `ClusterIP` Service only | `http://mcp-server.<namespace>.svc.cluster.local/mcp`. Only works when AgentMinder runs on the same cluster |

Set `mcp_istio_sidecar: true` when the load balancer talks Istio mTLS to
backends. Notes for Avi (AKO) are in
[docs/avi-ako-notes.md](docs/avi-ako-notes.md).

The MCP server does no authentication of its own. Anything that can reach
its address can call it, so restrict that address to the AgentMinder gateway
in anything beyond a demo.

On a cluster with mesh-wide Istio `STRICT` mTLS, leave `mcp_istio_sidecar`
at `false` for `loadbalancer`: a sidecar pod resets the plain HTTP the load
balancer sends. Switching `mcp_expose` does not delete what the other mode
created. Run `teardown.yml` before changing it.

## License

[MIT](LICENSE)

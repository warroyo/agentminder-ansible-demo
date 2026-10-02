# Exposing the MCP server with Avi (AKO)

For clusters where the Avi Kubernetes Operator (AKO) provides load balancing
and runs with `istioEnabled: true`. Settings for the Gateway API path:

```yaml
mcp_expose: gateway
mcp_gateway_class: avi-lb
mcp_istio_sidecar: true
```

The default `mcp_expose: loadbalancer` works on the same cluster. LoadBalancer
Services and Gateways can be used side by side. What differs is the sidecar
setting, because of Istio rather than AKO:

| `mcp_expose` | Traffic reaching the pod | `mcp_istio_sidecar` |
|---|---|---|
| `gateway` | Istio mTLS from the Avi pool | `true` |
| `loadbalancer` | plain HTTP | `false` |

With mesh-wide `PeerAuthentication` mode `STRICT`, a pod with a sidecar
answers plain HTTP with `Connection reset by peer`, so the single MCP pod in
this demo serves one path at a time.

The gateway path has four requirements.

1. **AKO may not serve LoadBalancer Services.** With
   `defaultLBController: false` the cluster's cloud provider handles them
   instead. Through AKO, use a `Gateway` (`gatewayClassName: avi-lb`) plus
   `HTTPRoute`.
2. **The route needs a hostname.** AKO does not program a route with no
   hostname (`Accepted=False NoMatchingListenerHostname`). The hostname must
   also resolve from wherever AgentMinder runs; `sslip.io` names always do. The playbook creates the Gateway, reads its address, and
   sets `amdemo-mcp-<address-with-dashes>.sslip.io` on the listener and route.
   On later runs it reuses the hostname from the existing Gateway, so the
   listener is not reset on each run. Set `mcp_hostname` to use a real DNS
   name instead.
3. **The MCP pod needs an Istio sidecar.** With `istioEnabled: true`, Avi
   pools connect to backends with Istio mTLS. A pod without a sidecar gets
   `503` from Avi, even though the TCP health monitor shows it up.
   `mcp_istio_sidecar: true` adds `sidecar.istio.io/inject: "true"` to the
   pod. With Istio CNI installed the pod still passes Pod Security
   `restricted`.
4. **AKO must be fully started.** If routes fail with
   `ProgrammingFailed: PKIProfile object not found!`, restart the AKO pod
   (`avi-system/ako-0`).

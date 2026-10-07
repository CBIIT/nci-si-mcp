# Deployment views

These diagrams complement the [architecture views](../ARCHITECTURE.md) and the executable
[container runbook](container.md). Local views describe the supported prototype. The Cloud One
view is a **proposed reference layout**, not deployed infrastructure or an approved AWS design.
Solid arrows show calls, reads or output; dashed arrows show operator-supplied configuration
and assets. Labels identify protocols and storage permissions.

## Local stdio

The MCP client launches and owns one server process. No listening HTTP port is required. A
stdio stream can retain its first implicit NCIt release; a one-shot CLI command resolves per
call. A local index is optional for live content tools but required for semantic/hybrid search.

```mermaid
---
config:
  theme: neutral
  look: classic
  layout: dagre
  flowchart:
    wrappingWidth: 260
---
flowchart TB
    subgraph Host["Developer workstation"]
        Client["Desktop MCP client"]
        Server["Python server process<br/>nci-si-mcp serve"]
        Settings["Environment settings"]
        Index[("Local SQLite<br/>writable data directory")]
        Model["Embedding provider"]
        Logs["Client log capture"]
        Client -->|"launch + stdio MCP"| Server
        Settings -. "process environment" .-> Server
        Server -->|"SQLite I/O"| Index
        Server -->|"embed query"| Model
        Server -->|"JSON stderr"| Logs
    end
    Upstream["NCI upstreams<br/>EVS · caDSR · Shared SI"]
    Server -->|"HTTP(S)"| Upstream
```

The index is built and evaluated through separate [CLI commands](../QUICKSTART.md#build-a-small-local-index).
Fixture mode redirects upstream requests to the local fixture server; it does not manufacture
successful live caDSR responses. See [client configuration](../QUICKSTART.md#connect-a-client).

## Local container over HTTP

This is the runbook's loopback-only Docker deployment. The image defaults to stateless HTTP
and a required index. It loads an externally supplied model offline, and never builds or
activates an index while serving. The root filesystem is read-only; `/data` and `/tmp` are
writable, and `/model-cache` is mounted read-only. Plain local Python HTTP also works with
`serve --transport streamable-http`; unlike the image, it defaults to stateful sessions and
does not require an index for readiness.

```mermaid
---
config:
  theme: neutral
  look: classic
  layout: dagre
  flowchart:
    wrappingWidth: 260
---
flowchart LR
    Client["Local MCP client"]
    subgraph Host["Local container host"]
        Port["Loopback publish<br/>127.0.0.1:8000"]
        Env["Environment file"]
        Data[("Index snapshot<br/>writable /data mount")]
        Cache["Model cache<br/>read-only mount"]
        subgraph Image["Container · UID 65532"]
            Server["PID 1: Python server<br/>0.0.0.0:8000"]
            Scratch["/tmp tmpfs"]
            Server -->|"temporary files"| Scratch
        end
        Logs["Container log driver"]
        Port -->|"port 8000"| Server
        Env -. "startup env" .-> Server
        Server -->|"SQLite I/O"| Data
        Server -->|"offline load"| Cache
        Server -->|"JSON stderr"| Logs
    end
    Upstream["NCI upstream origins"]
    Client -->|"HTTP MCP"| Port
    Server -->|"HTTP(S)"| Upstream
```

Host/Origin admission applies even on loopback and to health probes. `/health` checks the
process; `/ready` checks local index/model compatibility, not upstream availability. The
default image has no client authentication configured. Keep it private until the approved
identity integration is supplied. Allow 20 seconds for SIGTERM shutdown; commands and mount
requirements are in the [runbook](container.md#start-with-external-assets).

## Cloud One reference layout

The intended hosting environment is NCI CBIIT Cloud One (AWS). The hosting team still chooses
compute/orchestration, network topology, TLS termination, identity integration, secret and
artifact services, and deployment roles. The boxes below specify responsibilities without
selecting ECS, EKS, an AWS identity provider, or a shared filesystem. No such infrastructure
is provisioned by this repository.

```mermaid
---
config:
  theme: neutral
  look: classic
  layout: dagre
  flowchart:
    wrappingWidth: 260
---
flowchart TB
    Client["Authorized MCP applications"]
    subgraph Cloud["Cloud One / AWS · proposed workload boundary"]
        Edge["Ingress / load balancer<br/>TLS + identity boundary"]
        subgraph A["Replica A"]
            ServerA["Stateless MCP HTTP"]
            AssetsA[("Local SQLite copy<br/>+ read-only model")]
            ServerA -->|"local I/O"| AssetsA
        end
        subgraph B["Replica B"]
            ServerB["Stateless MCP HTTP"]
            AssetsB[("Local SQLite copy<br/>+ read-only model")]
            ServerB -->|"local I/O"| AssetsB
        end
        Logs["Log collection"]
        Egress["Controlled upstream egress"]
        Edge -->|"any ready replica"| ServerA
        Edge -->|"any ready replica"| ServerB
        ServerA -->|"JSON stderr"| Logs
        ServerB -->|"JSON stderr"| Logs
        ServerA -->|"HTTP(S)"| Egress
        ServerB -->|"HTTP(S)"| Egress
    end
    Upstream["EVS · caDSR · Shared SI"]
    Client -->|"HTTPS MCP"| Edge
    Egress -->|"HTTP(S)"| Upstream
```

Operational constraints apply whichever AWS services implement these boxes:

- **Stage each replica before readiness.** Deploy a verified GHCR image digest, copy the
  prepared index and matching model, and inject settings and secrets through the environment.
  Each process exposes `/mcp`, `/health` and `/ready`; image evidence is attached to its release.
- **Require approved authentication.** Set `NCI_SI_HTTP_AUTH_MODE=required` and install the
  [governed HTTP integration](governed-http.md); absent integration fails before listening.
  The local default remains trusted-local. Configure actual Host/Origin authorities;
  forwarded identity headers never grant access. Production identity is still deferred/off.
- **Stateless replicas are independent.** An omitted NCIt release resolves per call. Name a
  release explicitly when calls must agree across replicas or time. For stateful handshake
  clients, route each session to its owning process; a different replica returns 404.
  [The routing sequence](transport.md#session-routing-sequence) shows that failure and recovery.
- **Serving storage is local to each replica.** Distribute a consistent SQLite backup,
  including the active build and retained predecessor; never share a live writable database
  across replicas over a network filesystem. Preserve the model identity used by the index.
  Build/evaluate/activate in an operator workspace, then roll out matching snapshots and models.
  Rollback activates the predecessor in that workspace and redistributes it. The server does
  not synchronize assets or sessions; an asset rollout can invalidate build-bound cursors.
- **Readiness gates local compatibility.** Probe each replica with an allowed Host header.
  Neither health endpoint guarantees live upstream service, issued caDSR credentials, or
  production retrieval quality. The container smoke uses a test-only model.
- **Publication and deployment remain separate.** The repository publishes verified public
  GHCR images. Cloud One ECR publication, AWS credentials/roles and infrastructure automation
  remain hosting work; this view does not implement or authorize them.

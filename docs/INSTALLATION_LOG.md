# ArgoCD GitOps Demo — Installation & Operations Log

Living log of every real command run to set this up, with actual output — not a description of
what *should* happen. Purpose: a learning record of installing ArgoCD, wiring a GitHub Actions
build pipeline, and getting a push-to-deploy GitOps loop working end to end.

**Target cluster:** an existing, persistent k3s home-lab cluster (NOT a disposable test cluster) —
control-plane node `thinkertoy192.168.1.32`, worker nodes `deepthought`, `foundation`, `neuron`
(the last shown `NotReady` at the time of writing, unrelated to this work). Already hosts
`monitoring` and `opensearch` namespaces before this work began.

---

## 1. Pre-flight: cluster discovery

This host (`damir@...`) turned out to be a `k3s-agent` (worker) already joined to this cluster —
not a fresh install target. Confirmed before doing anything:

```
$ systemctl list-unit-files | grep -i k3s
k3s-agent.service    enabled    enabled
```

```
$ kubectl get nodes -o wide
NAME                     STATUS     ROLES           AGE   VERSION
deepthought              Ready      <none>          59d   v1.35.5+k3s1
foundation               Ready      <none>          59d   v1.35.5+k3s1
neuron                   NotReady   <none>          52d   v1.35.5+k3s1
thinkertoy192.168.1.32   Ready      control-plane   79d   v1.35.5+k3s1
```

```
$ kubectl get namespaces
NAME              STATUS   AGE
default           Active   79d
kube-node-lease   Active   79d
kube-public       Active   79d
kube-system       Active   79d
monitoring        Active   54d
opensearch        Active   5d23h
```

```
$ kubectl cluster-info
Kubernetes control plane is running at https://192.168.1.32:6443
CoreDNS is running at https://192.168.1.32:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy
Metrics-server is running at https://192.168.1.32:6443/api/v1/namespaces/kube-system/services/https:metrics-server:https/proxy
```

**Decision:** reuse this cluster (confirmed with the user explicitly, since it's real persistent
infrastructure, not a throwaway) rather than provision a second, redundant k3s control plane on
this same host under existing memory pressure (see "Resource notes" below).

## 2. Resource notes (context for why this mattered)

Before starting, this host was at ~800MB free RAM with swap fully exhausted (VS Code, Firefox,
OpenSearch, 4 concurrent Claude Code sessions, a redundant bare-metal Neo4j process alongside a
dockerized one). User closed VS Code and Firefox, which recovered headroom to ~5.1GB free /
8.9GB available before any cluster work began. Docker containers themselves were never the real
consumer (biggest was 508MB; most were 3-10MB each) — worth remembering for next time: check
`ps aux --sort=-%mem` before assuming Docker is the problem.

---

## 3. Installing ArgoCD (core components, no HA)

```
$ kubectl create namespace argocd
namespace/argocd created
```

First attempt with plain client-side apply failed on one resource — the `applicationsets.argoproj.io`
CRD is large enough that `kubectl apply`'s client-side `last-applied-configuration` annotation
exceeds Kubernetes' 262144-byte annotation size limit (a known issue with this particular CRD,
not specific to this cluster):

```
$ kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
...
The CustomResourceDefinition "applicationsets.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
```

**Fix:** use server-side apply instead (documented ArgoCD workaround) — it doesn't store the
whole previous config as a client-side annotation:

```
$ kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml --server-side --force-conflicts
customresourcedefinition.apiextensions.k8s.io/applications.argoproj.io serverside-applied
customresourcedefinition.apiextensions.k8s.io/applicationsets.argoproj.io serverside-applied
customresourcedefinition.apiextensions.k8s.io/appprojects.argoproj.io serverside-applied
... (all 58 resources applied cleanly)
```

**Waiting for rollout:**

```
$ kubectl -n argocd wait --for=condition=Available deployment --all --timeout=180s
deployment.apps/argocd-applicationset-controller condition met
deployment.apps/argocd-dex-server condition met
deployment.apps/argocd-notifications-controller condition met
deployment.apps/argocd-redis condition met
deployment.apps/argocd-repo-server condition met
deployment.apps/argocd-server condition met

$ kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=180s
partitioned roll out complete: 1 new pods have been updated...
```

**Final state — all 7 pods Running, scheduled across the cluster's other nodes (`foundation`,
`thinkertoy`), not this local host:**

```
$ kubectl -n argocd get pods -o wide
NAME                                                READY   STATUS    RESTARTS      NODE
argocd-application-controller-0                     1/1     Running   0             foundation
argocd-applicationset-controller-568dfdf75b-ht6dq   1/1     Running   0             thinkertoy192.168.1.32
argocd-dex-server-856bcdf9ff-z6s4k                  1/1     Running   5 (88s ago)   thinkertoy192.168.1.32
argocd-notifications-controller-6b4fd8f59-g4ht2     1/1     Running   0             thinkertoy192.168.1.32
argocd-redis-54c57dd6ff-pj7cr                        1/1     Running   0             thinkertoy192.168.1.32
argocd-repo-server-fd55df7c-v6vdj                   1/1     Running   0             thinkertoy192.168.1.32
argocd-server-6cd5f98457-mrt5x                       1/1     Running   0             foundation
```

`argocd-dex-server` had 5 restarts during startup (likely a slow-starting dependency it
liveness-probed against before it was ready) but stabilized to `1/1 Running` on its own — not
investigated further since it self-resolved.

## 4. Initial admin credentials

```
$ kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

**Security note, deliberately not logged here:** the actual password value is NOT recorded in
this file, or anywhere else in this repo. This directory is going to become a git repo pushed to
GitHub (see the GitHub Actions section below) — committing a real admin credential to a cluster
that also hosts unrelated production-ish workloads (`monitoring`, `opensearch` namespaces) would
be a real secret leak, not a hypothetical one.

**Done as a real follow-up, not left as a TODO:**

```
$ curl -sSL -o argocd-linux-amd64 https://github.com/argoproj/argo-cd/releases/latest/download/argocd-linux-amd64
$ sudo install -m 555 argocd-linux-amd64 /usr/local/bin/argocd
$ argocd version --client
argocd: v3.5.0+e95e1be

$ kubectl -n argocd port-forward svc/argocd-server 18443:443 &
Forwarding from 127.0.0.1:18443 -> 8080
# (ports 8080 and 8888 were already taken by unrelated pre-existing services on this
#  host -- docker-proxy and an otelcol-contrib instance, confirmed via `sudo ss -ltnp`
#  before picking a different port, rather than fighting over ports blindly)

$ argocd login 127.0.0.1:18443 --username admin --password <initial-password> --insecure
'admin:login' logged in successfully

$ argocd account update-password --current-password <initial-password> --new-password <new-password>
Password updated

$ kubectl -n argocd delete secret argocd-initial-admin-secret
secret "argocd-initial-admin-secret" deleted from argocd namespace

$ argocd account get-user-info
Logged In: true
Username: admin
```

The new password is stored at `~/.secrets/argocd-admin-password` (`chmod 600`), **outside** the
`~/ARGOCD` project directory entirely, so it can never accidentally be swept into a `git add .`
of this repo. The original initial-admin secret no longer exists in the cluster.

---

## 5. Minimal app + manifests

`app/main.py` — a trivial FastAPI app (`/` returns version + server timestamp, `/health` for the
readiness probe). Built and smoke-tested locally before writing any k8s manifests:

```
$ docker build -q -t argocd-gitops-demo:local-test .
sha256:c425f827a8e579c3bf02aef71c9cdc5481fd2a41eb8b78de83343201fb9a3d03

$ docker run -d --rm -p 18950:8000 argocd-gitops-demo:local-test
$ curl -s http://localhost:18950/
{"message":"hello from argocd-gitops-demo","version":"0.1.0","server_time":"2026-08-12T09:08:27.507658+00:00"}
$ curl -s http://localhost:18950/health
{"status":"ok"}
```

`manifests/namespace.yaml`, `service.yaml`, `deployment.yaml` written. The `image:` field in
`deployment.yaml` is a placeholder (`ghcr.io/OWNER/argocd-gitops-demo:PLACEHOLDER`) until the
real GitHub repo is created — this exact line is what CI will update on every push, and what
ArgoCD watches for drift against the live cluster.

Validated against the real cluster's API (server-side dry-run), namespace created for real since
it's needed regardless of the eventual image tag:

```
$ kubectl apply -f manifests/namespace.yaml
namespace/argocd-gitops-demo created

$ kubectl apply --dry-run=server -f manifests/service.yaml
service/argocd-gitops-demo created (server dry run)

$ kubectl apply --dry-run=server -f manifests/deployment.yaml
deployment.apps/argocd-gitops-demo created (server dry run)
```

(Note: server-side dry-run doesn't share state across resources in one batch or across separate
`kubectl` invocations — dry-running namespace+service+deployment together failed with
`namespaces "argocd-gitops-demo" not found` even right after the namespace dry-run reported
success, because dry-run never actually persists anything. Not a manifest bug; just why the
namespace was created for real before validating the rest.)

`service.yaml`/`deployment.yaml` themselves are **not** applied for real yet — that's ArgoCD's
job once it's wired to the repo (next sections), not something to apply ad-hoc outside the
GitOps flow this whole exercise is about.

---

## 6. GitHub repo + first real CI run

```
$ gh api user --jq .login
dmiruke-ai

$ gh repo create argocd-gitops-demo --public --description "..." --source=/home/damir/ARGOCD --remote=origin
https://github.com/dmiruke-ai/argocd-gitops-demo
```

`gitleaks detect` run against the whole project before the first commit — clean, no leaks.
Committed and pushed:

```
$ git push -u origin main
To https://github.com/dmiruke-ai/argocd-gitops-demo.git
 * [new branch]      main -> main
```

Since the initial commit touches `app/**`, `.github/workflows/build-and-deploy.yml`'s path
filter triggered it automatically — no manual `workflow_dispatch` needed:

```
$ gh run list --repo dmiruke-ai/argocd-gitops-demo --limit 5
in_progress   Initial commit: ...   build-and-deploy   main   push   31582068141   9s

$ gh run watch 31582068141 --repo dmiruke-ai/argocd-gitops-demo --exit-status
✓ build-and-deploy in 37s
  ✓ Checkout
  ✓ Compute lowercase image ref
  ✓ Log in to GHCR
  ✓ Build and push image
  ✓ Update manifests/deployment.yaml with the new image tag
  ✓ Commit and push the manifest update
```

Full pipeline succeeded first try, 37 seconds end to end. Pulled `git`, confirmed the bot's
follow-up commit updated `manifests/deployment.yaml`:

```
image: ghcr.io/dmiruke-ai/argocd-gitops-demo:350a317b810043f1710b33a4b54de9a7aaa7cf4d
```

Verified the image is real and actually pullable (not just "the workflow said success") by
pulling it directly, rather than trusting the green checkmark alone:

```
$ docker pull ghcr.io/dmiruke-ai/argocd-gitops-demo:350a317b810043f1710b33a4b54de9a7aaa7cf4d
Status: Downloaded newer image for ghcr.io/dmiruke-ai/argocd-gitops-demo:350a317b8...
```

(`gh api .../packages/.../versions` returned a 403 — the CLI token lacks `read:packages` scope.
Not investigated further since the direct `docker pull` is a more definitive check anyway.)

---

## 7. Wiring the ArgoCD Application — and a real pre-existing cluster bug found along the way

`argocd/application.yaml` applied by hand (not "app of apps" — kept simple for this demo):

```
$ kubectl apply -f argocd/application.yaml
application.argoproj.io/argocd-gitops-demo created
```

**It didn't sync.** `sync=Unknown health=` (empty), no resources appearing in the
`argocd-gitops-demo` namespace. `argocd-application-controller` logs showed repeated:

```
redis: connection pool: failed to dial after 5 attempts: dial tcp: lookup argocd-redis: i/o timeout
```

### Diagnosis (narrowed down step by step, not guessed)

1. Checked the `argocd-redis` Service/Endpoints — both correctly configured, pod healthy. Ruled
   out a broken Service.
2. Checked the `argocd-application-controller-network-policy` — `policyTypes: [Ingress]` only,
   no egress restriction. Ruled out NetworkPolicy blocking DNS.
3. Exec'd into `argocd-application-controller-0` (on node `foundation`) and tried
   `getent hosts kubernetes.default.svc.cluster.local` (not even redis-specific) — **also timed
   out.** Ruled out "specific to the redis hostname."
4. `argocd-application-controller-0`'s minimal image had no `wget`/`curl`/`nc` to test raw
   connectivity, so launched a `busybox` debug pod pinned to the same node
   (`kubectl run netdebug --overrides='{"spec":{"nodeName":"foundation"}}'`) and ran
   `nslookup ... 10.43.0.10` (CoreDNS's ClusterIP) — timed out. Tried again against CoreDNS's
   **direct pod IP** (`10.42.0.177`, bypassing the Service/kube-proxy entirely) — **also timed
   out.**

**Conclusion: raw pod-to-pod networking between node `foundation` and node
`thinkertoy192.168.1.32` (where CoreDNS runs) is broken on this cluster.** This is a pre-existing
condition — not caused by installing ArgoCD, and consistent with the `neuron` node's independent
`NotReady` status seen during initial cluster discovery (§1). Not something to attempt to fix by
guessing at CNI internals on someone's real home-lab network without a clear diagnosis path, so
this was raised as a decision point rather than worked around silently.

### Workaround applied (by agreement — not a fix for the underlying network issue)

Pinned every ArgoCD component to the single node already hosting most of them
(`thinkertoy192.168.1.32`, which also runs CoreDNS), avoiding the broken cross-node path
entirely:

```
$ for d in argocd-applicationset-controller argocd-dex-server argocd-notifications-controller \
           argocd-redis argocd-repo-server argocd-server; do
    kubectl -n argocd patch deployment "$d" --type=merge \
      -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"thinkertoy192.168.1.32"}}}}}'
  done
$ kubectl -n argocd patch statefulset argocd-application-controller --type=merge \
    -p '{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":"thinkertoy192.168.1.32"}}}}}'
```

All 7 pods rescheduled onto `thinkertoy192.168.1.32`. Redis connection errors disappeared from
the application-controller logs immediately.

**Forced an immediate sync** rather than waiting out the 2-minute resync period:

```
$ kubectl -n argocd annotate application argocd-gitops-demo argocd.argoproj.io/refresh=hard --overwrite
application.argoproj.io/argocd-gitops-demo annotated
```

**Result — real resources deployed:**

```
$ kubectl -n argocd get application argocd-gitops-demo -o jsonpath='sync={.status.sync.status} health={.status.health.status}'
sync=Synced health=Healthy

$ kubectl -n argocd-gitops-demo get pods -o wide
argocd-gitops-demo-5cbd648955-7scmv   1/1   Running   0   32s   10.42.5.186   foundation
```

(Note: the *app's own* pod landed back on `foundation` — that's fine, since the deployed
workload itself doesn't need pod-to-pod calls to ArgoCD's components after deployment; only
ArgoCD's own internal components needed to be co-located.)

**Verified the app is actually serving traffic**, not just "Healthy" per ArgoCD's status:

```
$ kubectl -n argocd-gitops-demo port-forward svc/argocd-gitops-demo 18960:80 &
$ curl -s http://localhost:18960/
{"message":"hello from argocd-gitops-demo","version":"0.1.0","server_time":"2026-08-12T22:31:06.248494+00:00"}
$ curl -s http://localhost:18960/health
{"status":"ok"}
```

**Follow-up not done here, flagged for whenever the underlying network issue gets attention:**
the `foundation` ↔ `thinkertoy` pod-networking break likely affects more than ArgoCD — anything
scheduled across those two nodes needing to talk to each other would hit the same wall. Worth
investigating the CNI (flannel, bundled with k3s) directly — VXLAN/wireguard backend
misconfiguration between hosts is a common cause of exactly this symptom (Service and pod IPs
both unreachable cross-node, single-node traffic fine).

---

## 8. Exposing the UI persistently (not just a temporary port-forward)

`argocd-server` already *is* the UI (same component serves the API and the web UI on one port) —
"installing the UI" here meant exposing it in a way that doesn't require an active
`kubectl port-forward` every time.

Considered Traefik (already running in this cluster, `kubectl get ingressclass` shows it
available) but **deliberately avoided it**: Traefik's pods are spread across all 4 nodes
(`svclb-traefik-*` DaemonSet pods, each with a striking restart count — 32 to 146 restarts —
another sign this cluster's cross-node networking has been flaky for a while, consistent with
sec.7's finding). Routing through a Traefik pod on a different node than `argocd-server`
(pinned to `thinkertoy192.168.1.32`) risks the same cross-node wall.

**Used a NodePort instead**, additive to (not replacing) the official ClusterIP `argocd-server`
Service, deliberately targeting `argocd-server` pods directly:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: argocd-server-nodeport
  namespace: argocd
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: argocd-server
  ports:
    - name: https
      port: 443
      targetPort: 8080
      nodePort: 30443
```

```
$ kubectl apply -f argocd/argocd-server-nodeport.yaml
service/argocd-server-nodeport created
```

**Verified for real, not just "Service created":**

```
$ curl -sk --max-time 8 -o /dev/null -w "HTTP %{http_code}\n" https://192.168.1.32:30443/
HTTP 200

$ curl -sk --max-time 8 https://192.168.1.32:30443/ | head -c 300
<!doctype html><html lang="en"><head>...<title>Argo CD</title>...
```

**Access:** `https://192.168.1.32:30443/` (self-signed cert — browser will warn, that's expected
for this in-cluster default cert). **Important caveat:** this only works reliably from a host
that can route to `192.168.1.32` directly, and only because `argocd-server` is pinned to that
exact node — connecting via a different node's IP would hit the same cross-node networking bug
from sec.7. Login: `admin` / the rotated password in `~/.secrets/argocd-admin-password` (never
committed to this repo).

---

*(Sections below are appended as each step actually happens.)*

# ArgoCD — What It Is and How It Works

## The problem it solves

Traditional CI/CD pipelines *push* changes: a pipeline builds an artifact, then runs
`kubectl apply` (or `helm upgrade`) directly against the cluster, using credentials the CI system
holds. That means your CI system needs cluster-admin-ish access, and the cluster's actual state
can drift from what's declared in git without anyone noticing (someone `kubectl edit`s something
by hand, and now git no longer describes reality).

**GitOps flips this to a pull model.** A controller running *inside* the cluster continuously
watches a git repo and reconciles the cluster's actual state to match what's declared there. Git
becomes the single source of truth; the cluster converges to it automatically, not because
something pushed to it.

**ArgoCD is that controller**, built specifically for Kubernetes.

## Core concepts

- **Application** — ArgoCD's central custom resource. Declares: which git repo + path to watch,
  which cluster + namespace to deploy into, and the sync policy. One `Application` = one thing
  being kept in sync.
- **Source** — the git repo/path (or Helm chart, or Kustomize overlay) ArgoCD reads manifests
  from.
- **Destination** — the target cluster + namespace.
- **Sync** — the act of reconciling: diff the live cluster state against the git-declared state,
  apply what's different. Can be manual (click/CLI-triggered) or automated (ArgoCD notices a new
  commit and syncs on its own, typically within ~3 minutes by default, or instantly with a
  webhook).
- **Sync status** — `Synced` (live state matches git) or `OutOfSync` (they've diverged — either
  git changed and hasn't been applied yet, or someone changed the live cluster by hand).
- **Health status** — separate from sync status. A `Deployment` can be `Synced` but `Degraded`
  (e.g. correctly applied, but the pod is crash-looping) — sync tells you "did we apply the
  right thing," health tells you "is the right thing actually working."

## Architecture

```
git repo (manifests)
      │
      │  polled (default) or webhook-notified
      ▼
┌─────────────────────────────────────────┐
│  ArgoCD (running inside the cluster)     │
│                                           │
│  repo-server        — clones/renders     │
│                        manifests          │
│  application-controller                  │
│                     — diffs live vs.     │
│                        desired state,     │
│                        applies changes    │
│  api-server         — CLI/UI/API          │
│  (redis)            — caching              │
└─────────────────────────────────────────┘
      │
      │  applies via the Kubernetes API
      ▼
   cluster resources (Deployments, Services, ...)
```

For this demo, only the core controller components are installed — no Dex (SSO), no
notifications controller, no HA replicas — to keep footprint minimal on a resource-constrained
host.

## Why this fits the "build-once/promote" pipeline model

This connects directly to the `deployment-metrics-platform` lab's DIRECTIVE.md §13
(build-once/promote-through-environments): CI's job stops at *building and pushing an immutable,
tagged artifact* and *updating a manifest to reference that tag*. CI should never run
`kubectl apply` itself. ArgoCD is what turns "the manifest now says tag X" into "the cluster now
runs tag X" — the deployment step is decoupled from the build step, and the only thing CI needs
write access to is the manifests repo (or a values file), not the cluster's credentials.

## Typical push-to-deploy flow (what this demo wires up)

```
1. Developer pushes code change
        │
2. GitHub Actions: build image, push to a registry, tag == commit SHA
        │
3. GitHub Actions: update the manifest (e.g. deployment.yaml's image: tag) and
   commit that change back to the repo (or a separate manifests repo)
        │
4. ArgoCD notices the new commit (poll or webhook)
        │
5. ArgoCD diffs: live cluster still running old tag, git says new tag → OutOfSync
        │
6. ArgoCD applies the diff (auto-sync) → cluster now runs the new image
        │
7. Health check: is the new pod Running/Ready? → Healthy or Degraded
```

Step 2-3 is "CI" in the traditional sense. Step 4-7 is what ArgoCD adds — the actual deployment
is driven by the cluster pulling from git, not the pipeline pushing to the cluster.

## Access model used in this demo

ArgoCD runs with its own in-cluster credentials against the same cluster it's installed in (no
cross-cluster or cross-account access needed for this simple case). The GitHub Actions side only
ever needs: (a) push access to build/push a container image to a registry, and (b) write access
to the manifests it updates. It never touches `kubectl` or holds a kubeconfig — that's the whole
point of the pull model.

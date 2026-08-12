# argocd-gitops-demo

A minimal push-to-deploy GitOps loop: GitHub Actions builds and pushes a container image on every
push to `main`, updates the k8s manifest to reference the new image tag, and ArgoCD (running in
a k3s cluster) picks up the change and deploys it automatically.

See [`docs/ARGOCD.md`](docs/ARGOCD.md) for how ArgoCD/GitOps works conceptually, and
[`docs/INSTALLATION_LOG.md`](docs/INSTALLATION_LOG.md) for the real commands and output from
setting all of this up (a learning log, not a description of what *should* happen).

## Layout

- `app/` — the demo FastAPI app (`main.py`, `Dockerfile`, `requirements.txt`)
- `manifests/` — plain k8s YAML (`namespace.yaml`, `service.yaml`, `deployment.yaml`) — what
  ArgoCD syncs against
- `.github/workflows/` — the CI pipeline: build, push to GHCR, update `deployment.yaml`'s image
  tag, commit back to this repo
- `docs/` — conceptual + operational documentation

## Flow

```
push to main
   -> GitHub Actions builds the image, tags it with the commit SHA
   -> pushes to ghcr.io
   -> updates manifests/deployment.yaml's image: line
   -> commits that change back to this repo
   -> ArgoCD notices the new commit, diffs against the live cluster
   -> auto-syncs -> new pod running the new image
```

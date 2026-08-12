from datetime import datetime, timezone
from fastapi import FastAPI

VERSION = "0.2.0"

app = FastAPI(title="argocd-gitops-demo")


@app.get("/")
def index() -> dict:
    return {
        "message": "hello from argocd-gitops-demo",
        "version": VERSION,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

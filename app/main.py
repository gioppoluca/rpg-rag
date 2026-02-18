from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.ui.routes import router as ui_router

tags_metadata = [
    {"name": "campaigns", "description": "Create and manage campaigns (1 campaign = 1 KB)."},
    {"name": "workspaces", "description": "Create and manage workspaces (chat topics) within a campaign."},
    {"name": "sources", "description": "Configure source folders/files for ingestion."},
    {"name": "kb", "description": "Knowledge base ingestion and retrieval (documents/chunks)."},
    {"name": "ingest", "description": "Ingestion runs history and per-file actions."},
]

app = FastAPI(
    title="RPG KB",
    version="0.2.0",
    openapi_tags=tags_metadata,
)

# Keep CORS permissive in dev; tighten in prod as needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.include_router(ui_router, prefix="/ui")


@app.get("/", include_in_schema=False)
def root_redirect():
    """Redirect to the web UI."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/ui/campaigns", status_code=302)


@app.get("/health", tags=["kb"])
def health():
    return {"ok": True}

import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # Ensure models are registered on Base.metadata
from app.database import Base, engine
from app.routers import contacts, users
from app.ws import router as ws_router

app = FastAPI(title="Calling App Signaling Backend")

# Enable CORS for cross-origin frontend clients (Flutter, WebRTC, React)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """Create all database tables on application startup."""
    Base.metadata.create_all(bind=engine)


# Include REST API and WebSocket Routers
app.include_router(users.router)
app.include_router(contacts.router)
app.include_router(ws_router)


@app.get("/")
def health_check():
    """Health check endpoint returning status ok."""
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    # Render injects the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

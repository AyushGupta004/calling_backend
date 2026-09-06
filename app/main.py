import asyncio
import logging
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # Ensure models are registered on Base.metadata
from app.database import Base, engine
from app.routers import contacts, turn, users
from app.ws import router as ws_router
from app.connection_manager import manager

logger = logging.getLogger("calling.main")

stale_call_sweep_task = None

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
async def on_startup():
    """Create all database tables on application startup and verify TURN configuration."""
    Base.metadata.create_all(bind=engine)

    turn_host = os.environ.get("TURN_HOST", turn.DEFAULT_TURN_HOST).strip()
    if not turn.is_turn_configured(turn_host):
        logger.warning(
            "⚠️  WARNING: TURN_HOST is not configured or using default placeholder ('%s'). "
            "WebRTC media relay (TURN) will fail for peers on restrictive/mobile networks! "
            "Configure TURN_HOST and TURN_STATIC_AUTH_SECRET in your environment.",
            turn_host or "EMPTY",
        )
    else:
        logger.info("TURN server relay configured at: %s", turn_host)

    global stale_call_sweep_task
    stale_call_sweep_task = asyncio.create_task(manager.sweep_stale_calls())


@app.on_event("shutdown")
async def on_shutdown():
    global stale_call_sweep_task
    if stale_call_sweep_task:
        stale_call_sweep_task.cancel()
        await asyncio.gather(stale_call_sweep_task, return_exceptions=True)
        stale_call_sweep_task = None


# Include REST API and WebSocket Routers
app.include_router(users.router)
app.include_router(contacts.router)
app.include_router(turn.router)
app.include_router(ws_router)


@app.get("/")
def health_check():
    """Health check endpoint returning status ok."""
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok", "turn_configured": turn.is_turn_configured()}


@app.head("/health")
def health_head():
    return




if __name__ == "__main__":
    # Render injects the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

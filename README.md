# Calling App Signaling Backend

FastAPI signaling backend for 1-to-1 WebRTC calling applications with PostgreSQL persistence and in-memory WebSocket routing, ready for deployment on [Render](https://render.com).

---

## Project Structure

```
calling_backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app setup, CORS, lifespan DB initialization, router mounts
│   ├── database.py              # SQLAlchemy engine/session, auto-fixes Render postgres://, get_db()
│   ├── models.py                # User & Contact SQLAlchemy models with constraints
│   ├── schemas.py               # Pydantic v2 schemas for requests, responses & signaling
│   ├── connection_manager.py    # In-memory WebSocket connection registry & presence tracking
│   ├── ws.py                    # Real-time WebSocket endpoint (/ws/{user_id}) & call routing
│   └── routers/
│       ├── __init__.py
│       ├── users.py             # User registration, search, and online status
│       └── contacts.py          # Contact list management (add, list, delete)
├── main.py                      # Root entrypoint re-exporting app for uvicorn
├── render.yaml                  # Infrastructure-as-Code blueprint for Render (Web + PostgreSQL)
└── requirements.txt             # Project dependencies
```

---

## Database Models

- **User (`users` table)**:
  - `id`: UUID string primary key.
  - `name`: User's display name.
  - `username`: Unique, indexed string handle.
  - `created_at`: UTC timestamp.
- **Contact (`contacts` table)**:
  - `id`: Auto-incrementing integer primary key.
  - `user_id`: Foreign key referencing `users.id` (on delete cascade).
  - `contact_id`: Foreign key referencing `users.id` (on delete cascade).
  - `created_at`: UTC timestamp.
  - **Unique constraint**: `(user_id, contact_id)` prevents duplicate contact entries.

---

## Local Development

1. **Activate Virtual Environment**:
   ```bash
   # Windows
   venv\Scripts\activate
   # Linux / macOS
   source venv/bin/activate
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run Server**:
   ```bash
   uvicorn main:app --reload
   ```
   *Note: If `DATABASE_URL` is not set in environment or `.env`, it automatically falls back to local SQLite (`calling_app.db`).*

4. **Interactive API Documentation**:
   - Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
   - ReDoc: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## Deploying to Render

### Method 1: Blueprint Deployment (Recommended)
1. Push this repository to GitHub/GitLab.
2. In the [Render Dashboard](https://dashboard.render.com/), click **New** -> **Blueprint**.
3. Select your repository. Render will automatically parse [render.yaml](file:///d:/calling_backend/render.yaml) and provision:
   - A managed **PostgreSQL Database** (`calling-db`).
   - A **FastAPI Web Service** (`calling-backend`) with `DATABASE_URL` automatically injected.
4. Click **Apply**.

### Method 2: Manual Deployment
1. **Create PostgreSQL Database on Render**:
   - Go to **New** -> **PostgreSQL**.
   - Copy the **Internal Database URL** (e.g. `postgres://user:password@dpg-.../dbname`).
2. **Create Web Service**:
   - Go to **New** -> **Web Service** and link your repo.
   - **Runtime**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Under **Environment Variables**, add:
     - `DATABASE_URL`: *(paste the Internal Database URL from step 1)*
   - `database.py` automatically handles converting `postgres://` to `postgresql://` required by SQLAlchemy 2.0.

---

## WebSocket Signaling Protocol (`/ws/{user_id}`)

Connect to:
`ws://<host>/ws/{user_id}` (or `wss://<host>/ws/{user_id}` in production).

On connection, the user is registered in the in-memory `ConnectionManager`.

### 1. Initiate Call (`call_request`)
Caller sends:
```json
{
  "type": "call_request",
  "to_user_id": "<callee_user_id>"
}
```
- If callee is offline: caller receives `{"type": "call_failed", "reason": "offline"}`.
- If callee or caller is already in an active call: caller receives `{"type": "call_failed", "reason": "busy"}`.
- If callee is available: server generates a `call_id` (`uuid4`), marks both users as busy, and sends to the callee:
  ```json
  {
    "type": "incoming_call",
    "from_user_id": "<caller_user_id>",
    "call_id": "<call_id>"
  }
  ```

### 2. Callee Responds
- **Accept**: Callee sends:
  ```json
  {
    "type": "call_accepted",
    "call_id": "<call_id>",
    "to_user_id": "<caller_user_id>"
  }
  ```
  Caller receives `{"type": "call_accepted", "call_id": "<call_id>"}`.

- **Reject**: Callee sends:
  ```json
  {
    "type": "call_rejected",
    "call_id": "<call_id>",
    "to_user_id": "<caller_user_id>"
  }
  ```
  Caller receives `{"type": "call_rejected", "call_id": "<call_id>"}` and busy state is cleared for both users.

### 3. WebRTC SDP & ICE Candidate Exchange
Senders must be actively engaged in `call_id`:
- **Offer**: `{"type": "offer", "call_id": "<call_id>", "to_user_id": "<peer_id>", "sdp": {...}}`
- **Answer**: `{"type": "answer", "call_id": "<call_id>", "to_user_id": "<peer_id>", "sdp": {...}}`
- **ICE Candidate**: `{"type": "ice_candidate", "call_id": "<call_id>", "to_user_id": "<peer_id>", "candidate": {...}}`

### 4. Terminate Call (`call_ended`)
Either party sends:
```json
{
  "type": "call_ended",
  "call_id": "<call_id>",
  "to_user_id": "<peer_id>"
}
```
Peer receives `{"type": "call_ended", "call_id": "<call_id>"}` and busy status is cleared.

### 5. Abrupt Disconnection
If a user disconnects during an active call, their partner receives:
```json
{
  "type": "call_ended",
  "reason": "peer_disconnected",
  "call_id": "<call_id>"
}
```

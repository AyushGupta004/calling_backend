# Deployment Guide for Render

This guide walks you through deploying the Calling App Signaling Backend to [Render](https://render.com) using a managed PostgreSQL database.

---

## 1. Create a Render PostgreSQL Database

1. Go to your [Render Dashboard](https://dashboard.render.com/).
2. Click **New +** and select **PostgreSQL**.
3. Configure your database settings:
   - **Name**: `calling-db` (or any preferred name)
   - **Database**: `calling_db`
   - **User**: `calling_user`
   - **Region**: Select the region closest to your users.
   - **Plan**: Free or Starter.
4. Click **Create Database**.
5. Once provisioned, scroll down to the **Connections** section and copy the **Internal Database URL** (e.g. `postgres://calling_user:...@dpg-...:5432/calling_db`).

---

## 2. Create a Render Web Service

1. Click **New +** and select **Web Service**.
2. Connect your Git repository (GitHub or GitLab).
3. Fill in the service configuration:
   - **Name**: `calling-backend`
   - **Language / Runtime**: `Python`
   - **Region**: Choose the same region as your database instance for minimal latency.
   - **Branch**: `main` (or your primary branch)
   - **Build Command**:
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     uvicorn app.main:app --host 0.0.0.0 --port $PORT
     ```
4. Configure Environment Variables:
   - Click **Add Environment Variable**.
   - Key: `DATABASE_URL`
   - Value: Paste the **Internal Database URL** copied from Step 1.
5. Click **Create Web Service**.

---

## 3. Automatic Database Initialization & Dialect Handling

- **Dialect Auto-Fix**: Render provides database connection strings beginning with `postgres://`. The backend in `app/database.py` automatically normalizes this to `postgresql://` as required by SQLAlchemy 2.0.
- **Table Creation**: On server startup, the application runs `Base.metadata.create_all(bind=engine)` to automatically create all required tables (`users` and `contacts`) without requiring manual migration runs.
- **Health Check**: Render's health probe checks `GET /`, which returns `{"status": "ok"}` with HTTP 200.
- **WebSockets on Render**: WebSockets work natively over HTTPS/WSS on the standard port at `wss://<your-service-name>.onrender.com/ws/{user_id}`.

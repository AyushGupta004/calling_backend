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
   - `DATABASE_URL`: Paste the **Internal Database URL** copied from Step 1.
   - `TURN_HOST`: Domain or public IP of your Coturn server (e.g. `turn.yourdomain.com`).
   - `TURN_STATIC_AUTH_SECRET`: Secret key matching `static-auth-secret` in your `turnserver.conf`.
   - `TURN_TTL` *(optional)*: Credential validity duration in seconds (defaults to `3600`).
5. Click **Create Web Service**.

---

## 3. Automatic Database Initialization & Dialect Handling

- **Dialect Auto-Fix**: Render provides database connection strings beginning with `postgres://`. The backend in `app/database.py` automatically normalizes this to `postgresql://` as required by SQLAlchemy 2.0.
- **Table Creation**: On server startup, the application runs `Base.metadata.create_all(bind=engine)` to automatically create all required tables (`users` and `contacts`) without requiring manual migration runs.
- **Health Check**: Render's health probe checks `GET /` and `GET /health`, which return `{"status": "ok"}` with HTTP 200.
- **WebSockets on Render**: WebSockets work natively over HTTPS/WSS on the standard port at `wss://<your-service-name>.onrender.com/ws/{user_id}`.

---

## 4. Coturn Server Setup (WebRTC Relay)

To allow calls to traverse symmetric NATs and mobile networks (CGNAT), run [Coturn](https://github.com/coturn/coturn) on a VPS or cloud instance with a public IP.

### Firewall & Ports
Ensure the following ports are allowed in your cloud provider's firewall / security groups:
- `3478/udp` and `3478/tcp` (Standard STUN/TURN)
- `5349/udp` and `5349/tcp` (STUN/TURN over TLS)
- `49152-65535/udp` (Dynamic relay port range for WebRTC media streams)

### Option A: Using Docker Compose (Recommended)
A ready-to-run configuration is provided in the `coturn/` directory of this repo:
1. Copy `coturn/` to your server.
2. Edit `turnserver.conf`:
   - Set `external-ip=<your-public-server-ip>`
   - Set `realm=<your-domain-or-ip>`
   - Set `static-auth-secret=<matching-TURN_STATIC_AUTH_SECRET>`
3. Run:
   ```bash
   cd coturn
   docker compose up -d
   ```

### Option B: Native System Package (Ubuntu / Debian)
```bash
sudo apt update && sudo apt install -y coturn
```
Edit `/etc/turnserver.conf`:
```conf
# Listening ports
listening-port=3478
tls-listening-port=5349

# Public IP or domain
realm=turn.yourdomain.com
listening-ip=0.0.0.0
external-ip=<your-public-server-ip>

# Short-lived REST API credentials authentication
use-auth-secret
static-auth-secret=<matching-TURN_STATIC_AUTH_SECRET>

# Dynamic UDP relay ports
min-port=49152
max-port=65535

# Security & logging
fingerprint
lt-cred-mech
no-cli
verbose
```
Enable and start the service:
```bash
sudo systemctl enable coturn
sudo systemctl restart coturn
```

---

## 5. Render Environment Variables

> [!IMPORTANT]
> Because `render.yaml` sets `sync: false` on `TURN_HOST`, Render **will not automatically set or overwrite this variable for you**. You **must** manually set your real values in the Render Dashboard, otherwise the backend will remain unconfigured and issue STUN-only responses.

In your [Render Dashboard](https://dashboard.render.com/) -> **calling-backend** -> **Environment**:
1. `TURN_HOST`: Set to your real Coturn server's public IP or domain (e.g. `203.0.113.10` or `turn.yourdomain.com`).
2. `TURN_STATIC_AUTH_SECRET`: Copy the generated secret from Render or paste your custom secret, ensuring it matches `static-auth-secret` in your `turnserver.conf`.
3. Save Changes and trigger a manual redeploy. The server boot log will show `TURN server relay configured at: <your-host>` instead of the unconfigured placeholder warning.

---

## 6. End-to-End Verification

### Method 1: Diagnostic Endpoint (`GET /turn-check`)
Hit the diagnostic endpoint on your deployed backend:
```bash
curl https://<your-render-app>.onrender.com/turn-check
```
When configured and reachable, it returns:
```json
{
  "turn_configured": true,
  "turn_host": "<your-real-turn-host>",
  "status": "healthy",
  "udp_port_3478_probe": {
    "reachable": true,
    "note": "Received STUN response from TURN server on UDP port 3478"
  }
}
```

### Method 2: Standalone Diagnostic CLI Script
Run the built-in diagnostic tool from your terminal:
```bash
# Test against your deployed Render service
python scripts/check_turn.py --backend-url https://<your-render-app>.onrender.com

# Or directly probe your TURN server's UDP port 3478
python scripts/check_turn.py --turn-host <your-server-ip> --port 3478
```

### Method 3: WebRTC Trickle ICE (Browser)
1. Query your deployed backend for credentials:
   ```bash
   curl https://<your-render-app>.onrender.com/turn-credentials?user_id=<registered-user-id>
   ```
2. Note the returned JSON payload:
   - `turnConfigured` should be `true`.
   - Copy the TURN URI: `turn:<your-host>:3478?transport=udp`
   - Copy the `username` and `credential`.
3. Open [WebRTC Trickle ICE](https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/).
4. Under **STUN or TURN URI**, enter `turn:<your-host>:3478?transport=udp`.
5. Enter the `username` and `password` (credential).
6. Click **Add Server**, select the newly added server, and click **Gather candidates**.
7. Confirm that a candidate with component **relay** appears in the results table (e.g. `typ relay raddr ...`).

### Method 4: CLI verification using `turnutils_uclient`
From any machine with the `coturn` package installed:
```bash
turnutils_uclient -u "<generated_username>" -w "<generated_credential>" -e <your-server-ip> -p 3478 <your-server-ip>
```
Confirm allocations succeed without `401 Unauthorized` or timeout errors.



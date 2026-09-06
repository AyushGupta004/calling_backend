# Project Rules and Guidelines

## Client Signaling Contract (`signaling_service.dart`)

When implementing `signaling_service.dart` (or any client-side WebRTC signaling service interacting with this backend):

Every outgoing message after `call_request` **must explicitly include `to_user_id`** in its JSON payload:
- `call_accepted`
- `call_rejected`
- `call_ended`
- `offer`
- `answer`
- `ice_candidate`

Never rely on the backend to infer the partner. Always match the explicit addressing shape of `call_request`.

### Expected Payloads Shape

| Event | Outgoing JSON Shape |
|---|---|
| `call_request` | `{"type": "call_request", "to_user_id": "<callee_id>"}` |
| `call_accepted` | `{"type": "call_accepted", "call_id": "<call_id>", "to_user_id": "<caller_id>"}` |
| `call_rejected` | `{"type": "call_rejected", "call_id": "<call_id>", "to_user_id": "<caller_id>"}` |
| `offer` | `{"type": "offer", "call_id": "<call_id>", "to_user_id": "<peer_id>", "sdp": {...}}` |
| `answer` | `{"type": "answer", "call_id": "<call_id>", "to_user_id": "<peer_id>", "sdp": {...}}` |
| `ice_candidate` | `{"type": "ice_candidate", "call_id": "<call_id>", "to_user_id": "<peer_id>", "candidate": {...}}` |
| `call_ended` | `{"type": "call_ended", "call_id": "<call_id>", "to_user_id": "<peer_id>"}` |

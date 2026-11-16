---
name: vike-alerts-webhook
description: Register an HTTPS webhook as an alert delivery channel — get HMAC-signed POST notifications when alerts fire (the only channel for Hyperliquid alerts); test it, rotate its secret, resend a delivery. Use when the user has a server / bot / Slack integration and wants programmatic alert delivery instead of email.
allowed-tools: Bash(vike-data:*)
---

# vike-alerts-webhook

## Registration

```bash
vike-data alerts register-webhook <https://your-server.com/vike-hook>
  [--secret <16+ char HMAC secret>]
  [--json]
```

**Every POST is signed** (HMAC-SHA256). `--secret` is optional: without it the server generates one and returns it as `secret` in the answer. Show it to the user right away (it is also under Account → Alerts → channels, Show secret) so the receiver can verify deliveries.

## Example

```bash
# Generate a strong secret first
openssl rand -hex 32
# → 7f5c8b...  (paste below)

vike-data alerts register-webhook https://api.mycoolbot.com/vike-alert \
  --secret 7f5c8b... \
  --json
# → { "ok": true, "channel_id": 42, "url_preview": "https://api.myc...", "signed": true }
```

Then create an alert with `--channel webhook` (alongside `email` if wanted; a Hyperliquid alert takes the webhook only). Registration sends one signed `test` event at once; the channel id is in `vike-data alerts channels`.

```bash
vike-data alerts test-webhook <channel_id>     # send the signed test event again
vike-data alerts rotate-secret <channel_id>    # only when the user explicitly asks: new secret, the old one keeps signing for 24 hours
vike-data alerts events --json                 # what fired, with each delivery's status
vike-data alerts resend <delivery_id>          # send one delivery again (up to 15 days)
```

## What we POST to your URL

```http
POST /vike-hook HTTP/1.1
Content-Type: application/json
User-Agent: vike-alerts/1.0
X-Vike-Timestamp: 1716658923
X-Vike-Signature: t=1716658923,sig=a3f8b2c...

{"alert_id":42,"alert_label":"Big USDC moves","alert_type":"transfer","triggered_at":"2026-05-25T17:42:00+00:00","chain":"eth","token":"USDC","value_usd":1234567.89,"from_address":"0x...","to_address":"0x...","tx_hash":"0x..."}
```

## How to verify the signature (Python)

```python
import hmac, hashlib, time

def verify(secret, headers, body):
    ts = headers.get("X-Vike-Timestamp", "")
    # During a secret rotation the header carries TWO sig= values: accept if ANY matches.
    sigs = [p.split("=", 1)[1] for p in headers.get("X-Vike-Signature", "").split(",") if p.startswith("sig=")]
    if not ts or not sigs:
        return False
    # Reject replays older than 5 min
    if abs(time.time() - int(ts)) > 300:
        return False
    expected = hmac.new(
        secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)
```

## Anti-patterns

- **Don't** lose the secret or skip verifying: every delivery is signed with it (yours, or the one the server generated), and without the check anyone who knows the URL can spoof alerts.
- **Don't** rotate the secret unless the user asks: the receiver must install the new one within 24 hours.
- **Don't** trust the body without verifying the signature. The URL doesn't authenticate the sender; the HMAC does.
- **Don't** use HTTP (only HTTPS) in production — the body is sensitive.
- **Don't** respond slowly — we time out after 10s. If your handler does heavy work, queue it (push to a job queue, return 200 immediately).

## Security checklist for your handler

1. Verify the HMAC signature in constant time (`hmac.compare_digest`, not `==`)
2. Reject if `X-Vike-Timestamp` is more than ~5 min old (replay protection)
3. Return `200` quickly; queue work async
4. Log full body for unsigned-but-suspicious requests
5. Rotate the secret if you suspect compromise — `vike-data alerts rotate-secret <channel_id>`

## Pairs well with

- `vike-data alerts create` — create the alert that fires to your webhook
- `vike-data alerts list` / `vike-data alerts channels` — confirm registration

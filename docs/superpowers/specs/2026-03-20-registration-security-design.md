# Registration Security: Email Verification + Bot Protection

**Date:** 2026-03-20
**Status:** Draft
**Approach:** Two-Phase Registration (Approach A)

## Overview

Add four layers of security to the registration flow:

1. **Cloudflare Turnstile** — bot challenge on the registration form
2. **Rate limiting** — 3 registration attempts per hour per IP
3. **Email OTP verification via Resend** — 6-digit code sent to user's email
4. **In-memory pending registration cache** — accounts only created after verification

Users must pass all four gates before an account is created in the database.

## Registration Flow

```
[Register Page]                      [Verify Page]                    [Home]
 email + password + Turnstile    →    6-digit OTP input            →   authenticated
       ↓                                    ↓
  POST /api/auth/register            POST /api/auth/verify-otp
       ↓                                    ↓
  1. Verify Turnstile token          1. Look up pending registration
  2. Rate limit check (3/hr/IP)      2. Check attempts < 5
  3. Check email not in users DB     3. Verify OTP (bcrypt compare)
  4. Check email not already pending 4. Create user in DB
  5. Hash password (bcrypt)          5. Issue JWT token
  6. Generate OTP, hash it (bcrypt)  6. Delete pending registration
  7. Store in PendingRegCache        7. Return AuthResponse
  8. Send OTP via Resend
  9. Return {email, message}
```

### Same-Email Detection

If a user opens a new tab and tries to register with an email that already has a pending OTP:

- No new pending registration is created
- Response tells frontend to redirect to `/verify?email=...`
- User can use the "resend code" button to get a fresh OTP

## Cloudflare Turnstile Integration

### Frontend (register page)

- Load Turnstile script: `<script src="https://challenges.cloudflare.com/turnstile/v0/api.js">`
- Render invisible/managed widget in the register form
- On submit, include the `cf-turnstile-response` token in the request body

### Backend verification

- New module: `backend/app/turnstile.py` (separate from `auth.py` to avoid mixing JWT and HTTP concerns)
- Function: `verify_turnstile_token(token: str) -> bool`
- Calls `POST https://challenges.cloudflare.com/turnstile/api/v0/siteverify` with token + secret key via `httpx`
- Returns true/false based on the `success` field in the response
- Called as the **first step** in the register endpoint — 400 immediately if it fails

### Development/Testing

For local development, use Cloudflare's test keys:
- Site key: `1x00000000000000000000AA` (always passes)
- Secret key: `1x0000000000000000000000000000000AA` (always passes)

The `verify_turnstile_token()` helper works unchanged with these test keys — no bypass logic needed.

### Config

```
# Backend (.env)
TURNSTILE_SECRET_KEY=...

# Frontend (.env.local)
NEXT_PUBLIC_TURNSTILE_SITE_KEY=...
```

The site key is public (browser-facing). The secret key is server-side only.

## Email OTP via Resend

### Sending

- New module: `backend/app/email_service.py`
- Function: `send_verification_email(email: str, otp: str)`
- Uses Resend Python SDK: `resend.Emails.send()`
- Clean, minimal HTML email template with the 6-digit code
- From address: `noreply@yourdomain.com` (or `onboarding@resend.dev` for development)

### OTP Generation & Storage

- 6-digit numeric code: `secrets.randbelow(900000) + 100000`
- OTP is **hashed with bcrypt** before storage (never stored in plaintext)
- Cache entry structure:

```python
{
    "email": str,
    "password_hash": str,        # bcrypt hash of password
    "otp_hash": str,             # bcrypt hash of 6-digit OTP
    "attempts": int,             # failed verification attempts (max 5)
    "resend_count": int,         # times OTP was resent (max 3)
    "expires_at": float,         # time.time() + 600 (10 minutes)
}
```

TTL is computed by comparing `time.time()` against `expires_at`. The `replace_otp` operation resets `expires_at` to `time.time() + 600`.

### Config

```
# Backend (.env)
RESEND_API_KEY=...
```

## Pending Registration Cache

### New file: `backend/app/pending_registration_cache.py`

Mirrors the existing `key_cache.py` pattern — in-memory Python dict with TTL.

### Operations

| Operation | Description |
|-----------|-------------|
| `store(email, entry)` | Store pending registration, keyed by email |
| `get(email) -> entry \| None` | Retrieve if exists and not expired |
| `remove(email)` | Delete after successful verification |
| `increment_attempts(email)` | Bump failed attempt counter |
| `replace_otp(email, new_otp_hash)` | For resend flow — resets TTL, bumps resend_count |

### TTL

10 minutes. Entries older than that return `None` and get cleaned up on access.

### Trade-offs

- Lost on server restart (acceptable — registration is a short-lived flow, user simply re-registers)
- No new infrastructure dependencies
- Follows existing project patterns

## API Schemas

### New Pydantic models in `backend/app/schemas.py`

```python
from pydantic import BaseModel, EmailStr, Field

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    turnstile_token: str

class RegisterResponse(BaseModel):
    message: str
    email: str

class OtpVerifyRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

class OtpResendRequest(BaseModel):
    email: EmailStr

class OtpResendResponse(BaseModel):
    message: str
```

Note: `EmailStr` requires `pydantic[email]` (which pulls in `email-validator`). Apply `EmailStr` to the existing `LoginRequest` as well.

The verify-otp endpoint returns the existing `AuthResponse` schema (same as login).

## API Endpoints

### `POST /api/auth/register` (modified)

```
Request:  RegisterRequest { email, password, turnstile_token }
Response: RegisterResponse { message: "Verification code sent", email }
Status:   200 success, 400 bad turnstile, 409 email taken, 429 rate limit
```

Rate limit: 3/hour per IP.

Steps: verify turnstile → validate email format → check email not in users table → check/handle pending → hash password → generate OTP → store pending → send email via Resend.

### `POST /api/auth/verify-otp` (new)

```
Request:  OtpVerifyRequest { email, otp }
Response: AuthResponse { token, user_id, provider_keys_loaded: false }
Status:   200 success, 400 wrong OTP, 410 expired/not found, 429 too many attempts
```

Rate limit: 10/minute per IP (prevents automated OTP spraying).

Steps: look up pending → check attempts < 5 → bcrypt compare OTP → create user in DB → issue JWT → delete pending entry.

Uses the same `AuthResponse` schema as login, so the frontend handles it identically.

### `POST /api/auth/resend-otp` (new)

```
Request:  OtpResendRequest { email }
Response: OtpResendResponse { message: "New code sent" }
Status:   200 success, 410 no pending registration, 429 resend limit reached
```

Max 3 resends per pending registration. Generates fresh OTP, resets the 10-minute TTL.

Rate limit: 10/minute per IP (prevents probing for pending emails and Resend API quota abuse).

### Error Detail Strings

| Endpoint | Status | `detail` value |
|----------|--------|----------------|
| register | 400 | `"Turnstile verification failed"` |
| register | 409 | `"Email already registered"` |
| register | 429 | `"Too many registration attempts"` |
| verify-otp | 400 | `"Invalid verification code"` |
| verify-otp | 410 | `"Verification expired or not found"` |
| verify-otp | 429 | `"Too many failed attempts"` |
| resend-otp | 410 | `"No pending registration found"` |
| resend-otp | 429 | `"Resend limit reached"` |

## Frontend Changes

### Register page (`/register`) — modified

- Add Turnstile widget (invisible/managed mode)
- On submit: include `turnstile_token` in POST body
- On success (200): redirect to `/verify?email=<email>`
- On 409 with pending registration: also redirect to `/verify?email=<email>`

### Verify page (`/verify`) — new

- Reads `email` from query params
- 6 input boxes for OTP digits (auto-advance on keystroke)
- "Resend code" link (disabled for 30s after sending, shows countdown)
- On submit: calls `POST /api/auth/verify-otp`
- On success: stores JWT via AuthContext, redirects to home
- Error states:
  - Wrong code: shake animation + "Invalid code"
  - Expired: redirect back to register
  - Too many attempts: message + redirect to register

### AuthContext (`AuthContext.tsx`) — modified

Updated `AuthContextValue` interface:

```typescript
interface AuthContextValue {
  // ... existing fields unchanged ...
  register: (email: string, password: string, turnstileToken: string) => Promise<{ email: string; message: string }>;
  verifyOtp: (email: string, otp: string) => Promise<void>;  // stores JWT on success
  resendOtp: (email: string) => Promise<{ message: string }>;
  // login, logout, getToken, isSignedIn, isLoaded — unchanged
}
```

- `register()` now accepts `turnstileToken` and returns `{ email, message }` instead of storing a JWT
- `verifyOtp()` calls verify-otp, stores the JWT in localStorage, sets `isSignedIn`
- `resendOtp()` calls resend-otp, returns the response message

### Login page — unchanged

Turnstile only on registration. Login has bcrypt's natural slowness as a rate limiter. Turnstile can be added to login later if needed.

## Error Handling & Edge Cases

| Scenario | Behavior |
|----------|----------|
| User closes browser, comes back | Pending registration expires after 10 min. They re-register. |
| Same email, new tab | Detects existing pending entry, redirects to verify page |
| Server restarts mid-verification | Pending cache lost. User gets "expired" error, re-registers. |
| 5 wrong OTP attempts | Pending entry locked. Must wait for expiry (10 min) and start over. |
| 3 resends exhausted | "Resend limit reached" message. Wait for expiry and re-register. |
| Email already in users table | 409 "Email already registered" — directs to login |
| Turnstile fails/times out | 400, form shows "Verification failed, please try again" |
| Resend API down | 500, form shows "Failed to send code, please try again" |
| Race condition: two requests, same email | `store()` overwrites — last one wins, only latest OTP is valid |

## Security Summary

| Layer | Protection |
|-------|-----------|
| Cloudflare Turnstile | Blocks bots at the front door |
| Rate limiting — register (3/hr/IP) | Prevents mass registration attempts |
| Rate limiting — verify-otp (10/min/IP) | Prevents automated OTP spraying |
| OTP hashed with bcrypt | Never stored in plaintext |
| 5-attempt cap | Prevents brute-forcing the 6-digit code |
| 10-minute TTL | Short window of exposure |
| 3 resend limit | Prevents email bombing |
| Password hashed before cache storage | Never in plaintext, even temporarily |
| Email validation (EmailStr) | Rejects invalid emails before processing |
| Password strength (8-128 chars) | Prevents trivially weak passwords |

### Brute-force analysis

A 6-digit OTP has 900,000 possible values. With the rate limit of 3 registrations/hr/IP and 5 attempts per registration, an attacker gets a maximum of 15 OTP guesses per IP per hour — a 0.0017% success rate. The 10/min rate limit on verify-otp further prevents rapid-fire guessing. Combined with Turnstile blocking automated requests, this is an acceptable risk profile.

A distributed attacker with many IPs gets proportionally more guesses, but the 5-attempt-per-registration cap is independent of IP — it's the primary defense. An attacker needs to pass Turnstile for each new registration, which is the bottleneck for automated attacks.

**Note:** The register endpoint contract changes (adds `turnstile_token` as required). Existing clients that don't send this field will receive 422 validation errors. This is intentional — the old unprotected registration flow is being replaced.

## New & Modified Files

### New files

| File | Purpose |
|------|---------|
| `backend/app/pending_registration_cache.py` | In-memory cache for pending registrations |
| `backend/app/email_service.py` | Resend SDK wrapper for sending verification emails |
| `backend/app/turnstile.py` | Cloudflare Turnstile token verification |
| `frontend/src/app/verify/page.tsx` | OTP verification page |

### Modified files

| File | Changes |
|------|---------|
| `backend/app/routers/auth_routes.py` | Modified register endpoint, new verify-otp and resend-otp endpoints |
| `backend/app/config.py` | Add `TURNSTILE_SECRET_KEY`, `RESEND_API_KEY` |
| `backend/app/schemas.py` | New request/response schemas for OTP flow |
| `frontend/src/app/register/page.tsx` | Add Turnstile widget, redirect to verify on success |
| `frontend/src/context/AuthContext.tsx` | New `verifyOtp()`, `resendOtp()` methods; modified `register()` |
| `backend/.env` / `backend/.env.example` | New env vars |
| `frontend/.env.local` / `frontend/.env.example` | New env var for Turnstile site key |

### Dependencies to add

| Package | Where | Purpose |
|---------|-------|---------|
| `resend` | `backend/requirements.txt` | Resend Python SDK for sending emails |
| `pydantic[email]` | `backend/requirements.txt` | Email validation via `EmailStr` (pulls in `email-validator`) |
| `httpx` | Already in `requirements.txt` | Used for Turnstile API verification — no action needed |

## Testing Strategy

### Backend tests

- `test_register_with_turnstile` — mock Turnstile API, verify valid token creates pending entry
- `test_register_invalid_turnstile` — verify 400 on bad token
- `test_register_duplicate_email` — verify 409 when email exists in users table
- `test_register_pending_email` — verify redirect response when email already pending
- `test_verify_otp_success` — verify account created, JWT returned, pending entry removed
- `test_verify_otp_wrong_code` — verify 400, attempts incremented
- `test_verify_otp_expired` — verify 410 after TTL
- `test_verify_otp_max_attempts` — verify 429 after 5 wrong attempts
- `test_resend_otp` — verify new OTP generated, TTL reset
- `test_resend_otp_limit` — verify 429 after 3 resends
- `test_pending_cache_ttl` — verify entries expire after 10 minutes
- `test_rate_limit_registration` — verify 429 after 3 registrations/hour

### Integration test

- Full flow: register → receive OTP (mock Resend) → verify → authenticated

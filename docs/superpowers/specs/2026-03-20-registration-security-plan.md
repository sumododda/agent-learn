# Implementation Plan: Registration Security

**Date:** 2026-03-20
**Spec:** `docs/superpowers/specs/2026-03-20-registration-security-design.md`

---

## Phase 0: Documentation & API Reference

### Allowed APIs (verified from official docs)

**Resend Python SDK (v2.25.0)**
```python
import resend
resend.api_key = "re_..."  # or set RESEND_API_KEY env var

params: resend.Emails.SendParams = {
    "from": "Name <noreply@domain.com>",
    "to": ["user@example.com"],
    "subject": "Subject",
    "html": "<p>Body</p>",
}
email: resend.Emails.SendResponse = resend.Emails.send(params)
# Returns: {"id": "uuid-string"}
```
- Exceptions: `resend.exceptions.ResendError` (base), `.ValidationError`, `.RateLimitError`
- Without verified domain: can only use `onboarding@resend.dev` and send to own account email
- Rate limit: 5 req/sec per team

**Cloudflare Turnstile — Server-Side**
```
POST https://challenges.cloudflare.com/turnstile/v0/siteverify
Body: { "secret": "...", "response": "TOKEN" }
Response: { "success": true/false, "error-codes": [...] }
```
- Tokens: single-use, max 2048 chars, valid 300 seconds
- Accepts `application/json` or `application/x-www-form-urlencoded`
- Test secret key (always passes): `1x0000000000000000000000000000000AA`

**Cloudflare Turnstile — Frontend (`@marsidev/react-turnstile` v1.4.2)**
```tsx
import { Turnstile, type TurnstileInstance } from '@marsidev/react-turnstile'
<Turnstile
  siteKey="..."
  onSuccess={(token) => setToken(token)}
  onError={(code) => console.error(code)}
  onExpire={() => ref.current?.reset()}
  options={{ theme: 'auto', size: 'normal' }}
/>
```
- Test site key (always passes): `1x00000000000000000000AA`

### Anti-Patterns to Avoid

- Do NOT use `resend.send()` — it's `resend.Emails.send()`
- Do NOT use `requests` for Turnstile — use `httpx` (already in requirements)
- Do NOT store OTP in plaintext — always hash with bcrypt via `pwd_context.hash()`
- Do NOT add `turnstile.py` logic into `auth.py` — keep concerns separated
- Do NOT use `created_at` for TTL — use `expires_at` matching `key_cache.py` pattern

---

## Phase 1: Backend — Config & Dependencies

### What to Implement

1. **Add `resend` and `pydantic[email]` to `backend/requirements.txt`**

2. **Add config fields to `backend/app/config.py`** — copy the existing `Settings` pattern:
   ```python
   TURNSTILE_SECRET_KEY: str = ""
   RESEND_API_KEY: str = ""
   RESEND_FROM_EMAIL: str = "onboarding@resend.dev"
   ```

3. **Add new schemas to `backend/app/schemas.py`**:
   ```python
   from pydantic import EmailStr, Field

   class RegisterRequest(BaseModel):  # MODIFY existing
       email: EmailStr
       password: str = Field(min_length=8, max_length=128)
       turnstile_token: str

   class RegisterResponse(BaseModel):  # NEW
       message: str
       email: str

   class OtpVerifyRequest(BaseModel):  # NEW
       email: EmailStr
       otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

   class OtpResendRequest(BaseModel):  # NEW
       email: EmailStr

   class OtpResendResponse(BaseModel):  # NEW
       message: str
   ```
   Also update `LoginRequest` to use `EmailStr`.

### Documentation References

- Config pattern: `backend/app/config.py` (entire file — copy the Settings class structure)
- Schema pattern: `backend/app/schemas.py` lines 187-200 (RegisterRequest, LoginRequest, AuthResponse)

### Verification Checklist

- [ ] `pip install -r requirements.txt` succeeds with new deps
- [ ] `from app.config import settings; settings.TURNSTILE_SECRET_KEY` accessible
- [ ] `from app.schemas import RegisterRequest, RegisterResponse, OtpVerifyRequest, OtpResendRequest, OtpResendResponse` imports work
- [ ] RegisterRequest rejects emails without @ symbol (EmailStr validation)
- [ ] RegisterRequest rejects passwords shorter than 8 characters

---

## Phase 2: Backend — Turnstile Verification Module

### What to Implement

1. **Create `backend/app/turnstile.py`** with a single async function:
   ```python
   import httpx
   from app.config import settings

   async def verify_turnstile_token(token: str) -> bool:
       async with httpx.AsyncClient() as client:
           resp = await client.post(
               "https://challenges.cloudflare.com/turnstile/v0/siteverify",
               json={"secret": settings.TURNSTILE_SECRET_KEY, "response": token},
               timeout=10.0,
           )
           result = resp.json()
           return result.get("success", False)
   ```

### Documentation References

- Turnstile siteverify API: `POST https://challenges.cloudflare.com/turnstile/v0/siteverify`
- Request body: `{"secret": str, "response": str}` — both required
- Response: `{"success": bool, "error-codes": [str]}`
- httpx async pattern: project already uses httpx in requirements.txt

### Verification Checklist

- [ ] `from app.turnstile import verify_turnstile_token` imports
- [ ] With test secret key `1x0000000000000000000000000000000AA`, calling `verify_turnstile_token("test-token")` returns `True`
- [ ] With invalid secret, returns `False`
- [ ] Function handles httpx timeout gracefully (returns `False`)

### Anti-Pattern Guards

- Do NOT put this in `auth.py` — separate module for Turnstile
- Do NOT use `requests` library — use `httpx.AsyncClient` (async-compatible)

---

## Phase 3: Backend — Email Service Module

### What to Implement

1. **Create `backend/app/email_service.py`**:
   ```python
   import resend
   from app.config import settings

   def _init_resend():
       resend.api_key = settings.RESEND_API_KEY

   def send_verification_email(email: str, otp: str) -> None:
       _init_resend()
       params: resend.Emails.SendParams = {
           "from": settings.RESEND_FROM_EMAIL,
           "to": [email],
           "subject": "Your verification code",
           "html": f"""
           <div style="font-family: sans-serif; max-width: 400px; margin: 0 auto; padding: 20px;">
               <h2 style="color: #7c3aed;">Verify your email</h2>
               <p>Your verification code is:</p>
               <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; padding: 16px; background: #f3f4f6; border-radius: 8px; text-align: center;">{otp}</div>
               <p style="color: #6b7280; font-size: 14px; margin-top: 16px;">This code expires in 10 minutes.</p>
           </div>
           """,
       }
       resend.Emails.send(params)
   ```

### Documentation References

- Resend SDK: `resend.Emails.send(params: SendParams)` — params is a TypedDict
- Required fields: `to` (list of strings), `subject`, `html` or `text`
- `from` field requires verified domain or use `onboarding@resend.dev` for testing
- Exceptions: `resend.exceptions.ResendError` base class

### Verification Checklist

- [ ] `from app.email_service import send_verification_email` imports
- [ ] With valid Resend API key, calling `send_verification_email("your@email.com", "123456")` sends an email
- [ ] Email arrives with the correct OTP code and styling

### Anti-Pattern Guards

- Do NOT use `resend.send()` — the correct call is `resend.Emails.send()`
- Do NOT store the raw OTP in any log or response — it's only passed to the email

---

## Phase 4: Backend — Pending Registration Cache

### What to Implement

1. **Create `backend/app/pending_registration_cache.py`** — mirror `key_cache.py` pattern:
   ```python
   from dataclasses import dataclass, field
   from datetime import datetime, timezone, timedelta

   TTL_SECONDS = 600  # 10 minutes
   MAX_ATTEMPTS = 5
   MAX_RESENDS = 3

   @dataclass
   class _PendingEntry:
       email: str
       password_hash: str
       otp_hash: str
       attempts: int = 0
       resend_count: int = 0
       expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS))

   _cache: dict[str, _PendingEntry] = {}

   def store(email: str, password_hash: str, otp_hash: str) -> None
   def get(email: str) -> _PendingEntry | None  # returns None if expired, cleans up
   def remove(email: str) -> None
   def increment_attempts(email: str) -> int  # returns new count
   def replace_otp(email: str, new_otp_hash: str) -> bool  # resets expires_at, bumps resend_count
   ```

### Documentation References

- Cache pattern: `backend/app/key_cache.py` (entire file) — dataclass with `expires_at`, module-level dict, expiration check in `get()`
- TTL check: `if datetime.now(timezone.utc) > entry.expires_at` then `del _cache[key]; return None`

### Verification Checklist

- [ ] `store("test@example.com", "hash", "otp_hash")` creates entry
- [ ] `get("test@example.com")` returns entry before expiry
- [ ] `get("test@example.com")` returns `None` after TTL expires
- [ ] `increment_attempts` increments and returns new count
- [ ] `replace_otp` resets `expires_at` and increments `resend_count`
- [ ] `replace_otp` returns `False` when `resend_count >= MAX_RESENDS`
- [ ] `remove` deletes the entry

### Anti-Pattern Guards

- Do NOT use `created_at` — use `expires_at` (matches `key_cache.py`)
- Do NOT store plaintext OTP — only the bcrypt hash

---

## Phase 5: Backend — Auth Route Modifications

### What to Implement

1. **Modify `POST /api/auth/register`** in `backend/app/routers/auth_routes.py`:
   - Change request model to new `RegisterRequest` (with `turnstile_token`)
   - Change response model to `RegisterResponse`
   - Add `@limiter.limit("3/hour")` decorator
   - Flow: verify turnstile → check email not in DB → check pending cache → hash password → generate OTP → hash OTP → store pending → send email → return RegisterResponse
   - If email already pending: return same RegisterResponse (idempotent, don't leak info)

2. **Add `POST /api/auth/verify-otp`** endpoint:
   - Request: `OtpVerifyRequest`, Response: `AuthResponse`
   - Add `@limiter.limit("10/minute")` decorator
   - Flow: get pending → check attempts < 5 → bcrypt verify OTP → create User → create JWT → remove pending → return AuthResponse
   - On wrong OTP: increment attempts, return 400
   - On expired/not found: return 410
   - On max attempts: return 429

3. **Add `POST /api/auth/resend-otp`** endpoint:
   - Request: `OtpResendRequest`, Response: `OtpResendResponse`
   - Add `@limiter.limit("10/minute")` decorator
   - Flow: get pending → check resend_count < 3 → generate new OTP → hash → replace_otp → send email → return response

4. **Add OTP generation helper** in the same file:
   ```python
   import secrets
   def _generate_otp() -> str:
       return str(secrets.randbelow(900000) + 100000)
   ```

### Documentation References

- Current register endpoint: `backend/app/routers/auth_routes.py` lines 36-63
- Rate limiting decorator: `from app.limiter import limiter` then `@limiter.limit("3/hour")`
- Rate limiter requires `request: Request` parameter: `from fastapi import Request` — add `request: Request` to endpoint signature
- Password hashing: `pwd_context.hash(password)` and `pwd_context.verify(password, hash)` from `backend/app/auth.py`
- JWT creation: `create_access_token(str(user.id))` from `backend/app/auth.py`
- User model: `User(email=..., password_hash=...)` from `backend/app/models.py`

### Verification Checklist

- [ ] `POST /api/auth/register` with valid Turnstile token returns 200 with `{message, email}`
- [ ] `POST /api/auth/register` without Turnstile token returns 400
- [ ] `POST /api/auth/register` with existing email returns 409
- [ ] `POST /api/auth/register` with pending email returns 200 (idempotent)
- [ ] 4th registration in an hour from same IP returns 429
- [ ] `POST /api/auth/verify-otp` with correct OTP returns JWT (AuthResponse)
- [ ] `POST /api/auth/verify-otp` with wrong OTP returns 400, increments attempts
- [ ] `POST /api/auth/verify-otp` after 5 wrong attempts returns 429
- [ ] `POST /api/auth/verify-otp` with expired entry returns 410
- [ ] `POST /api/auth/resend-otp` generates new OTP and sends email
- [ ] `POST /api/auth/resend-otp` after 3 resends returns 429
- [ ] User exists in DB after successful verify-otp
- [ ] JWT from verify-otp is valid and `get_current_user()` works with it

### Anti-Pattern Guards

- Do NOT create the user in the register endpoint — only in verify-otp
- Do NOT return different responses for "email pending" vs "new registration" — prevents info leaking
- Do NOT forget the `request: Request` parameter for rate-limited endpoints

---

## Phase 6: Frontend — Turnstile & Register Page

### What to Implement

1. **Install `@marsidev/react-turnstile`**:
   ```bash
   npm install @marsidev/react-turnstile
   ```

2. **Modify `frontend/src/app/register/page.tsx`**:
   - Import `Turnstile` and `TurnstileInstance` from `@marsidev/react-turnstile`
   - Add `useRef<TurnstileInstance>(null)` for the widget
   - Add `turnstileToken` state
   - Render `<Turnstile>` widget inside the form with `onSuccess`, `onError`, `onExpire`
   - On submit: include `turnstileToken` in the register call
   - On success: redirect to `/verify?email=${encodeURIComponent(email)}` instead of `/`
   - Disable submit button until Turnstile token is available

3. **Modify `frontend/src/context/AuthContext.tsx`**:
   - Change `register` signature: `(email: string, password: string, turnstileToken: string) => Promise<{ email: string; message: string }>`
   - `register()` no longer stores JWT — returns the response body
   - Add `verifyOtp: (email: string, otp: string) => Promise<void>` — calls `/api/auth/verify-otp`, stores JWT, sets `isSignedIn`
   - Add `resendOtp: (email: string) => Promise<{ message: string }>` — calls `/api/auth/resend-otp`

### Documentation References

- `@marsidev/react-turnstile` component: `<Turnstile siteKey="..." onSuccess={(token) => ...} />`
- Ref methods: `ref.current?.reset()` to re-run challenge
- Current register page: `frontend/src/app/register/page.tsx`
- Current AuthContext: `frontend/src/context/AuthContext.tsx` — `register` callback pattern
- API call pattern: `fetch(API_BASE + path, { method: 'POST', headers, body: JSON.stringify(...) })`

### Verification Checklist

- [ ] Turnstile widget renders on register page
- [ ] Submit button is disabled until Turnstile passes
- [ ] On successful registration, redirects to `/verify?email=...`
- [ ] AuthContext `register()` sends turnstileToken in request body
- [ ] AuthContext `register()` returns `{email, message}` (not JWT)
- [ ] AuthContext `verifyOtp()` stores JWT in localStorage
- [ ] AuthContext `resendOtp()` calls the resend endpoint

### Anti-Pattern Guards

- Do NOT load Turnstile script manually — `@marsidev/react-turnstile` auto-injects it
- Do NOT store JWT on register — only on verify-otp success

---

## Phase 7: Frontend — OTP Verification Page

### What to Implement

1. **Create `frontend/src/app/verify/page.tsx`**:
   - Read `email` from URL search params: `useSearchParams().get('email')`
   - 6 individual `<input>` elements for OTP digits, each `maxLength={1}`
   - Auto-advance: on input, focus next field. On backspace, focus previous.
   - Auto-submit when all 6 digits entered
   - "Resend code" link with 30-second cooldown timer
   - Loading state during verification
   - Error states:
     - 400 (wrong code): shake animation + "Invalid code" message, clear inputs
     - 410 (expired): redirect to `/register` with message
     - 429 (too many attempts): show message, redirect to `/register` after delay
   - On success: `verifyOtp(email, otp)` stores JWT, redirect to `/`
   - If no email in URL params: redirect to `/register`

### Documentation References

- Current register page for styling/pattern: `frontend/src/app/register/page.tsx`
- AuthContext API calls: `frontend/src/context/AuthContext.tsx` — `fetch` + error handling pattern
- URL params in Next.js App Router: `import { useSearchParams } from 'next/navigation'`
- Styling: Tailwind classes matching existing purple accent theme

### Verification Checklist

- [ ] Page renders 6 OTP input boxes
- [ ] Typing a digit auto-advances to next box
- [ ] Backspace moves to previous box
- [ ] Auto-submits when all 6 digits are entered
- [ ] Correct OTP → authenticated, redirected to home
- [ ] Wrong OTP → "Invalid code" error, inputs cleared
- [ ] Resend button disabled for 30 seconds with countdown
- [ ] No email param → redirects to /register
- [ ] Expired code → redirects to /register

### Anti-Pattern Guards

- Do NOT use a single text input — use 6 individual inputs for OTP UX
- Do NOT forget `'use client'` directive — page uses hooks

---

## Phase 8: Backend — Tests

### What to Implement

1. **Create/update `backend/tests/test_auth_registration.py`** with tests:

   **Turnstile tests** (mock httpx):
   - `test_register_valid_turnstile` — mock returns `{"success": true}`, expect 200
   - `test_register_invalid_turnstile` — mock returns `{"success": false}`, expect 400

   **Registration flow tests** (mock Turnstile + Resend):
   - `test_register_creates_pending_entry` — verify cache entry exists after register
   - `test_register_duplicate_email` — existing user, expect 409
   - `test_register_pending_email_idempotent` — email already pending, expect 200
   - `test_register_rate_limit` — 4th request in an hour, expect 429

   **OTP verification tests**:
   - `test_verify_otp_success` — correct OTP creates user, returns JWT
   - `test_verify_otp_wrong_code` — expect 400, attempts incremented
   - `test_verify_otp_expired` — expect 410
   - `test_verify_otp_max_attempts` — 5 wrong guesses then expect 429
   - `test_verify_otp_rate_limit` — 11th request in a minute, expect 429

   **Resend OTP tests**:
   - `test_resend_otp_success` — new OTP sent, TTL reset
   - `test_resend_otp_limit` — 4th resend, expect 429
   - `test_resend_otp_no_pending` — expect 410

   **Cache tests**:
   - `test_pending_cache_ttl` — entry expires after TTL
   - `test_pending_cache_store_and_get` — basic store/retrieve
   - `test_pending_cache_increment_attempts` — counter increments
   - `test_pending_cache_replace_otp` — new hash, reset TTL, bump resend_count

   **Integration test**:
   - `test_full_registration_flow` — register → verify OTP → use JWT to access protected endpoint

### Documentation References

- Existing test patterns: `backend/tests/test_crypto.py`, `backend/tests/test_courses.py`, `backend/tests/test_provider_routes.py`
- Mocking: `unittest.mock.patch` or `pytest-mock`
- Test client: `from httpx import AsyncClient` with `app` fixture
- SQLite for tests: check existing test fixtures for database setup

### Verification Checklist

- [ ] All tests pass with `pytest backend/tests/test_auth_registration.py`
- [ ] Tests mock external services (Turnstile API, Resend API) — no real HTTP calls
- [ ] Integration test covers the full register → verify → authenticated flow

---

## Phase 9: Final Verification

### Checklist

- [ ] `pip install -r requirements.txt` — no errors
- [ ] `npm install` in frontend — no errors
- [ ] All existing tests still pass: `pytest backend/tests/`
- [ ] New tests all pass: `pytest backend/tests/test_auth_registration.py`
- [ ] Manual flow test: register → receive email → enter OTP → authenticated
- [ ] Grep for anti-patterns:
  - `grep -r "resend.send" backend/` — should find 0 results (correct API is `resend.Emails.send`)
  - `grep -r "created_at" backend/app/pending_registration_cache.py` — should find 0 results (use `expires_at`)
  - `grep -rn "otp" backend/app/ --include="*.py" | grep -iv "hash\|verify\|generate\|send\|replace\|increment\|test"` — check no plaintext OTP leaks in logs/responses
- [ ] Rate limiting works: 4th registration in an hour returns 429
- [ ] Invalid Turnstile token returns 400
- [ ] Email validation: registration with `"notanemail"` returns 422
- [ ] Password validation: registration with `"short"` returns 422

# Security Hardening Plan

> Generated from security review on 2026-03-27. Execute phases in order.

## Phase 0: Documentation Discovery Summary

### Alembic Migrations
- **Current HEAD:** `b9956d0cdc4a`
- **Async setup:** env.py uses `async_engine_from_config()` with `pool.NullPool`
- **Pattern:** All migrations hand-written (no autogenerate)
- **FK pattern example:** `sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE')` (see `46e9e861793c`)
- **Critical type mismatch:** `users.id` = `Uuid`, but `courses.user_id` / `learner_progress.user_id` / `chat_messages.user_id` = `Text(str)`. Must convert column type before adding FK.

### Slowapi Rate Limiting
- **Current:** `get_remote_address` — returns TCP peer IP (= GKE ingress, not real client)
- **Fix option A (simple):** Switch to `slowapi.util.get_ipaddr` — reads `X-Forwarded-For`
- **Fix option B (secure):** Add `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware` with trusted hosts, keep `get_remote_address`
- **GKE ingress:** GCE ingress DOES set `X-Forwarded-For` with original client IP

### CORS & Security Headers
- **Backend:** `allow_credentials=True` but frontend never uses `credentials: 'include'` in fetch calls — safe to remove
- **Frontend:** Comprehensive security headers already set in `next.config.ts` (CSP, HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy)
- **Backend:** No security headers set — API responses lack Referrer-Policy for SSE token URLs

### Password Validation
- **Current:** `Field(min_length=8, max_length=128)` — no complexity rules
- **No strength libraries installed** (no zxcvbn, etc.)
- **Pydantic pattern:** Use `@field_validator` decorator for custom validation
- **Frontend:** No client-side password validation beyond `required`

---

## Phase 1: Rate Limiter Fix + CORS Cleanup (Quick Wins)

**Priority:** High — rate limiting is currently broken in production behind GKE ingress

### Task 1.1: Fix rate limiter to use real client IP

**File:** `backend/app/limiter.py`

Change `get_remote_address` to `get_ipaddr`:
```python
from slowapi import Limiter
from slowapi.util import get_ipaddr

limiter = Limiter(key_func=get_ipaddr)
```

**Why not ProxyHeadersMiddleware:** The simpler approach is adequate for now. GKE ingress is the only path to the backend (network policy restricts private CIDR egress), so X-Forwarded-For spoofing from external clients won't bypass the ingress controller.

**Anti-pattern:** Do NOT use a custom key_func that checks CIDR ranges — GKE internal IPs change.

### Task 1.2: Remove `allow_credentials=True` from CORS

**File:** `backend/app/main.py` (lines 47-53)

Remove `allow_credentials=True` from the CORSMiddleware config. The frontend uses Bearer token auth via `Authorization` header, never `credentials: 'include'`.

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Task 1.3: Add Referrer-Policy header to SSE responses

**File:** `backend/app/routers/courses.py`

Add `"Referrer-Policy": "no-referrer"` to the headers dict on all `StreamingResponse` returns for SSE endpoints (pipeline_stream, discover_stream). This prevents the SSE token from leaking via Referrer headers if any redirect occurs.

There are 4 StreamingResponse returns in this file. Add the header to all of them.

### Verification Checklist
- [ ] `grep -r "get_remote_address" backend/` returns 0 results
- [ ] `grep "allow_credentials" backend/` returns 0 results
- [ ] `grep "Referrer-Policy" backend/app/routers/courses.py` returns results for all SSE endpoints
- [ ] Backend starts without errors
- [ ] Rate limit test: Hit `/api/auth/login` 11 times from same IP → 429 on 11th

---

## Phase 2: Password Strength & Login Lockout

**Priority:** Medium-High — prevents brute force attacks

### Task 2.1: Add server-side password complexity validation

**File:** `backend/app/schemas.py`

Add a `@field_validator` to `RegisterRequest` and `PasswordChangeRequest`:

```python
from pydantic import field_validator
import re

def _validate_password_strength(password: str) -> str:
    """Require at least one uppercase, one lowercase, one digit."""
    if not re.search(r'[A-Z]', password):
        raise ValueError('Password must contain at least one uppercase letter')
    if not re.search(r'[a-z]', password):
        raise ValueError('Password must contain at least one lowercase letter')
    if not re.search(r'[0-9]', password):
        raise ValueError('Password must contain at least one digit')
    return password
```

Apply to both `RegisterRequest.password` and `PasswordChangeRequest.new_password` using:
```python
_check_password_strength = field_validator('password')(_validate_password_strength)
```

**Anti-pattern:** Do NOT add a zxcvbn dependency for this. Simple regex rules are sufficient and have zero new dependencies.

### Task 2.2: Add per-email login attempt tracking

**File:** `backend/app/routers/auth_routes.py` (new in-memory tracker)

Create a simple failed login counter dict (similar to pending_registration_cache):
- Key: email (lowercase)
- Value: (attempt_count, first_attempt_at)
- After 5 failed attempts within 15 minutes: reject login with 429 "Too many attempts, try again later"
- Reset on successful login
- Auto-expire entries after 15 minutes

Add check at the start of the `login` endpoint, before password verification.

**Anti-pattern:** Do NOT lock the account permanently. Use a time-window approach (15 min).

### Task 2.3: Frontend password requirements hint

**File:** `frontend/src/app/register/page.tsx`

Add a small hint below the password field showing requirements: "At least 8 characters with uppercase, lowercase, and a digit."

No complex client-side validation needed — the server will enforce.

### Verification Checklist
- [ ] Register with "password" (no uppercase/digit) → 422 error
- [ ] Register with "Password1" → succeeds
- [ ] Login with wrong password 6 times → 429 on 6th attempt
- [ ] Wait 15 minutes (or reset counter) → login works again
- [ ] Password change with weak new password → 422 error
- [ ] Frontend shows requirements hint on register page

---

## Phase 3: Database Integrity — user_id Foreign Keys

**Priority:** Critical for data integrity, but non-breaking change

### Pre-flight: Data Audit

Before migration, verify all existing `user_id` values are valid UUIDs that exist in the `users` table. Run in a psql session or one-off script:

```sql
-- Check for orphaned course user_ids
SELECT id, user_id FROM courses
WHERE user_id IS NOT NULL
  AND user_id::uuid NOT IN (SELECT id FROM users);

-- Check for orphaned learner_progress user_ids
SELECT id, user_id FROM learner_progress
WHERE user_id::uuid NOT IN (SELECT id FROM users);

-- Check for orphaned chat_messages user_ids
SELECT id, user_id FROM chat_messages
WHERE user_id::uuid NOT IN (SELECT id FROM users);
```

If orphaned rows exist, delete them before migration.

### Task 3.1: Create Alembic migration — convert user_id columns and add FKs

**File:** New migration via `cd backend && alembic revision -m "add_user_id_foreign_keys"`

The migration must:
1. **Convert column types** from `Text` to `Uuid` (using `USING user_id::uuid`)
2. **Add FK constraints** referencing `users.id`
3. **Make `courses.user_id` non-nullable** (set to NOT NULL after conversion)

```python
def upgrade() -> None:
    # courses.user_id: Text -> Uuid, add FK
    op.execute("DELETE FROM courses WHERE user_id IS NULL")
    op.alter_column('courses', 'user_id',
                     type_=sa.Uuid(),
                     postgresql_using='user_id::uuid',
                     nullable=False)
    op.create_foreign_key('fk_courses_user_id', 'courses', 'users', ['user_id'], ['id'])

    # learner_progress.user_id: Text -> Uuid, add FK
    op.alter_column('learner_progress', 'user_id',
                     type_=sa.Uuid(),
                     postgresql_using='user_id::uuid',
                     nullable=False)
    op.create_foreign_key('fk_learner_progress_user_id', 'learner_progress', 'users', ['user_id'], ['id'])

    # chat_messages.user_id: Text -> Uuid, add FK
    op.alter_column('chat_messages', 'user_id',
                     type_=sa.Uuid(),
                     postgresql_using='user_id::uuid',
                     nullable=False)
    op.create_foreign_key('fk_chat_messages_user_id', 'chat_messages', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_chat_messages_user_id', 'chat_messages')
    op.alter_column('chat_messages', 'user_id', type_=sa.Text(), postgresql_using='user_id::text')

    op.drop_constraint('fk_learner_progress_user_id', 'learner_progress')
    op.alter_column('learner_progress', 'user_id', type_=sa.Text(), postgresql_using='user_id::text')

    op.drop_constraint('fk_courses_user_id', 'courses')
    op.alter_column('courses', 'user_id', type_=sa.Text(), nullable=True, postgresql_using='user_id::text')
```

### Task 3.2: Update ORM models to match

**File:** `backend/app/models.py`

Update the three models:

```python
# Course (line 30)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

# LearnerProgress (line 143)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

# ChatMessage (line 158)
user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
```

### Task 3.3: Update all code that passes user_id as string

The `get_current_user` dependency returns `str`. After this change, all code that sets `user_id` on Course, LearnerProgress, or ChatMessage must convert to `uuid.UUID` first.

**Files to update:**
- `backend/app/routers/courses.py` — `create_course` (line 100), `update_progress` (line 971-974)
- `backend/app/routers/chat.py` — `chat_stream` (line 109-116, 141-148)
- All query `.where(Course.user_id == user_id)` comparisons — these may need `uuid.UUID(user_id)` conversion

**Anti-pattern:** Do NOT change `get_current_user` to return UUID — that would require touching every endpoint. Convert at the point of model assignment instead.

### Verification Checklist
- [ ] Migration runs without errors on production data
- [ ] `\d courses` in psql shows `user_id` as `uuid` with FK constraint
- [ ] `\d learner_progress` in psql shows `user_id` as `uuid` with FK constraint
- [ ] `\d chat_messages` in psql shows `user_id` as `uuid` with FK constraint
- [ ] Create course → user_id stored as UUID, not string
- [ ] Create progress → user_id stored as UUID
- [ ] Send chat message → user_id stored as UUID
- [ ] All existing API endpoints still work (manual smoke test)

---

## Phase 4: Credential Validation & Cache Resilience

**Priority:** Medium

### Task 4.1: Add value length validation to provider credentials

**File:** `backend/app/schemas.py`

Add a `@field_validator` to `ProviderSaveRequest` and `ProviderUpdateRequest` that rejects credential values longer than 10,000 characters:

```python
@field_validator('credentials')
@classmethod
def validate_credential_values(cls, v):
    if v:
        for key, val in v.items():
            if isinstance(val, str) and len(val) > 10000:
                raise ValueError(f'Credential value for {key} exceeds maximum length')
    return v
```

### Task 4.2: Add resilient OTP verification for multi-replica

**File:** `backend/app/pending_registration_cache.py` + `backend/app/routers/auth_routes.py`

**Option A (minimal, recommended for now):** Add a fallback to the verify-otp endpoint. If the pending cache entry is not found in-memory, check if the user already exists in the database (another replica may have already verified them). Return a clear error message guiding the user to re-register if the entry is truly expired.

**Option B (future):** Replace in-memory cache with Redis. This is a larger change that requires adding a Redis dependency and deployment config.

For now, implement Option A and document the multi-replica limitation.

### Verification Checklist
- [ ] Save provider with a credential value > 10,000 chars → 422 error
- [ ] Save provider with normal API key → succeeds
- [ ] OTP verification failure message is clear and actionable

---

## Phase 5: Final Verification

### Task 5.1: Security header audit

Run a quick check that all expected headers are present:

```bash
# Backend SSE endpoints should have Referrer-Policy
curl -I https://learn.blekcipher.com/api/health

# Frontend should have CSP, HSTS, X-Frame-Options
curl -I https://learn.blekcipher.com/
```

### Task 5.2: Anti-pattern grep checks

```bash
# No remaining get_remote_address usage
grep -r "get_remote_address" backend/

# No allow_credentials in CORS
grep "allow_credentials" backend/app/main.py

# No Text user_id columns in models
grep "Mapped\[str" backend/app/models.py | grep user_id

# All credential dicts have validation
grep "credentials: dict" backend/app/schemas.py
```

### Task 5.3: Manual smoke test

1. Register new account with weak password → rejected
2. Register with strong password → succeeds
3. Login with wrong password 6 times → locked out
4. Login with correct password → succeeds
5. Create course → user_id is UUID in DB
6. Save provider credentials → works
7. SSE streams work → pipeline and discover
8. Rate limits work per-client, not globally

---

## Deferred / Not Addressed

These items were identified but deliberately deferred:

| Item | Reason |
|------|--------|
| RS256 JWT signing | HS256 is fine for single-service architecture. Only needed if tokens cross service boundaries. |
| Redis for registration cache | Requires new infrastructure. Acceptable risk at current scale (1-3 replicas). |
| Email send failure surfacing | Low impact — user can resend OTP. Logging is sufficient. |
| JWT in localStorage vs HttpOnly cookie | Standard SPA pattern. CSP headers mitigate XSS vector. |

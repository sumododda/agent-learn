# Design: Remove Trigger.dev and Clerk Dependencies

**Date**: 2026-03-19
**Status**: Approved
**Goal**: Eliminate two external service dependencies (Trigger.dev, Clerk) and replace with simple, in-process alternatives. Reduces the stack from 3 runtimes (Python + Node/Trigger + Next.js) to 2 (Python + Next.js).

---

## Part 1: Replace Trigger.dev with asyncio + in-memory dict

### Problem

Trigger.dev adds a Node.js runtime and external service dependency for what amounts to thin HTTP wrappers around Python backend functions. All 6 task files just POST to `/api/internal/*` endpoints. The real work happens in `backend/app/agent_service.py`.

### Solution

Move orchestration into a Python async function. Track progress in an in-memory dict. Frontend polls for status (already has this fallback).

### New file: `backend/app/pipeline.py`

```python
# In-memory progress store
_jobs: dict[str, PipelineStatus] = {}

@dataclass
class PipelineStatus:
    stage: str          # "planning", "researching", "verifying", "writing", "editing", "completed", "failed"
    section: int        # current section number (1-indexed)
    total: int          # total sections
    error: str | None

async def run_pipeline(course_id: str, session_factory):
    """Main orchestrator — equivalent to trigger/src/tasks/generate-course.ts

    Each pipeline step creates its own session via session_factory since a single
    session cannot span the full multi-minute pipeline duration.
    """
    # 1. discover_and_plan (sequential)
    # 2. research all sections (parallel via asyncio.gather)
    # 3. for each section: verify -> write -> edit (sequential)
    # 4. set course status completed/failed

# Track active tasks for graceful shutdown
_active_tasks: set[asyncio.Task] = set()
```

### Lifecycle management

Register a FastAPI shutdown handler to cancel running pipelines on server stop:

```python
@app.on_event("shutdown")
async def shutdown_pipelines():
    for task in _active_tasks:
        task.cancel()
    await asyncio.gather(*_active_tasks, return_exceptions=True)
```

### Changes to existing files

| File | Change |
|------|--------|
| `routers/courses.py` POST `/generate` | Replace Trigger.dev HTTP call with `asyncio.create_task(run_pipeline(...))` |
| `schemas.py` | Drop `run_id` from `GenerateResponse` |
| `routers/internal.py` | **Delete entirely** — internal endpoints only existed for Trigger.dev |
| `config.py` | Remove `TRIGGER_SECRET_KEY`, `TRIGGER_API_URL`, `INTERNAL_API_TOKEN` |
| Frontend `PipelineProgress.tsx` | Rewrite to poll `GET /courses/{id}` (remove `useRealtimeRun`) |
| Frontend `courses/[id]/page.tsx` | Remove `runId` state, remove `@trigger.dev/react-hooks` import |
| Frontend `package.json` | Remove `@trigger.dev/react-hooks` |
| Frontend `src/lib/types.ts` | Remove `run_id` from `GenerateResponse`, remove `PipelineMetadata` interface |
| `schemas.py` internal schemas (lines ~124-228) | Remove all `Internal*` request/response schemas (dead code after `internal.py` deletion) |
| `agent_service.py` | Remove legacy `_pipeline_status` dict and related comment block (~lines 36-49) — new `pipeline.py` owns status |
| `backend/tests/test_internal_api.py` | **Delete** — tests for deleted internal endpoints |
| `backend/.env.example` | Remove `TRIGGER_SECRET_KEY`, `TRIGGER_API_URL`, `INTERNAL_API_TOKEN` |
| `frontend/.env.example` | Remove `NEXT_PUBLIC_TRIGGER_PUBLIC_API_KEY` |

### Deleted

- Entire `trigger/` directory (~15 files)
- `backend/tests/test_internal_api.py`
- `TRIGGER_SECRET_KEY`, `TRIGGER_API_URL`, `INTERNAL_API_TOKEN`, `NEXT_PUBLIC_TRIGGER_PUBLIC_API_KEY` env vars

### Pipeline flow

Same logic as the TypeScript orchestrator, now in Python:

```
1. discover_and_plan(course_id)          — sequential
2. research_section(course_id, pos)      — parallel (asyncio.gather)
3. for each section:                     — sequential
     verify_section -> write_section -> edit_section
4. set course status completed/failed
```

### Progress tracking

- Dict keyed by `course_id` with `PipelineStatus` dataclass
- Frontend polls `GET /courses/{id}` every 3-5 seconds (existing endpoint, add pipeline status to response)
- Progress lost on server restart — acceptable for current scale

### Retries

Use `tenacity` library:
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2))
async def research_section_with_retry(course_id, section_pos, session):
    ...
```

Or simple try/except loops if we don't want the tenacity dependency.

---

## Part 2: Replace Clerk with simple JWT auth

### Problem

Clerk is used only for basic sign-in/sign-up and JWT user identification. No orgs, roles, MFA, webhooks, or user metadata. The backend just extracts `user_id` from the JWT `sub` claim. This is a paid external dependency for functionality that takes ~100 lines of code.

### Solution

Roll our own auth: users table + bcrypt password hashing + HS256 JWT tokens + custom React context.

### New database table

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);
```

New Alembic migration. Existing `user_id` foreign keys in Course, ChatMessage, LearnerProgress stay as-is.

### New file: `backend/app/routers/auth_routes.py`

```python
POST /api/auth/register   # email + password -> hash, insert user, return JWT
POST /api/auth/login       # email + password -> verify, return JWT
```

JWT payload: `{"sub": str(user.id), "exp": datetime}`, signed HS256 with `JWT_SECRET_KEY`.

### Rewrite: `backend/app/auth.py`

Replace Clerk JWKS + RS256 verification with:

```python
def get_current_user(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "")
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
    return payload["sub"]
```

Same interface — `Depends(get_current_user)` returns `user_id: str`. **No changes needed in any router files.**

### Config changes

Remove:
- `CLERK_JWKS_URL`, `CLERK_ISSUER`

Add:
- `JWT_SECRET_KEY: str` (random secret for signing)
- `JWT_EXPIRE_MINUTES: int = 1440` (24 hours default)

### New backend dependencies

- `passlib[bcrypt]` — password hashing
- Note: `PyJWT` is already installed (used by the existing Clerk auth code). Reuse it instead of adding `python-jose`.

### Frontend changes

| File | Change |
|------|--------|
| `src/context/AuthContext.tsx` | **New** — AuthProvider with `{ user, token, login, register, logout }`, stores JWT in localStorage |
| `src/app/layout.tsx` | Replace `ClerkProvider` with `AuthProvider`, replace `SignInButton`/`UserButton`/`Show` with custom components |
| `src/app/login/page.tsx` | **New** — simple email/password form |
| `src/app/register/page.tsx` | **New** — simple email/password form |
| All pages with `useAuth()` | Change import from `@clerk/nextjs` to `@/context/AuthContext` — `getToken()` call stays identical |
| `package.json` | Remove `@clerk/nextjs` |
| `src/proxy.ts` | **Delete or rewrite** — contains Clerk's `clerkMiddleware` which will crash without `@clerk/nextjs`. Replace with custom Next.js middleware if route protection desired, or delete (backend already validates tokens). |
| `backend/tests/test_auth.py` | **Rewrite** — tests reference Clerk-specific behavior, need to test new JWT register/login flow |
| `backend/.env.example` | Remove `CLERK_JWKS_URL`, `CLERK_ISSUER`, add `JWT_SECRET_KEY`, `JWT_EXPIRE_MINUTES` |
| `frontend/.env.example` | Remove `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL` |

### Token expiry handling

The `AuthContext` should handle expired tokens: check expiry before returning from `getToken()`, and redirect to `/login` on 401 responses. This replaces Clerk's automatic token refresh.

### Deleted

- `@clerk/nextjs` from frontend dependencies
- `src/app/sign-in/` and `src/app/sign-up/` directories (Clerk catch-all routes)
- `src/proxy.ts` (Clerk middleware)
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` frontend env vars
- `CLERK_JWKS_URL`, `CLERK_ISSUER` backend env vars

### Migration path for existing data

Existing `user_id` values in the DB are Clerk IDs (e.g., `user_2abc...`). Options:
- **(a) Wipe and start fresh** — simplest if this is dev data
- **(b) Insert matching user rows** — if preserving data matters

---

## Files touched summary

### Deleted (entire files/directories)
- `trigger/` (entire directory)
- `backend/app/routers/internal.py`
- `backend/tests/test_internal_api.py`
- `frontend/src/app/sign-in/`
- `frontend/src/app/sign-up/`
- `frontend/src/proxy.ts` (Clerk middleware)

### New files
- `backend/app/pipeline.py`
- `backend/app/routers/auth_routes.py`
- `backend/alembic/versions/xxx_create_users_table.py`
- `frontend/src/context/AuthContext.tsx`
- `frontend/src/app/login/page.tsx`
- `frontend/src/app/register/page.tsx`

### Modified files
- `backend/app/auth.py` (rewrite JWT verification)
- `backend/app/agent_service.py` (remove legacy `_pipeline_status` dict)
- `backend/app/config.py` (remove Clerk/Trigger/internal vars, add JWT vars)
- `backend/app/main.py` (register new auth router, remove internal router)
- `backend/app/schemas.py` (drop run_id, remove internal schemas, add auth schemas)
- `backend/app/routers/courses.py` (replace Trigger call with asyncio)
- `backend/requirements.txt` (add passlib[bcrypt], keep PyJWT)
- `backend/tests/test_auth.py` (rewrite for new JWT auth flow)
- `backend/.env.example` (swap env vars)
- `frontend/src/app/layout.tsx` (replace ClerkProvider)
- `frontend/src/app/courses/[id]/page.tsx` (remove trigger imports)
- `frontend/src/app/courses/[id]/learn/page.tsx` (change useAuth import)
- `frontend/src/app/page.tsx` (change useAuth import)
- `frontend/src/app/library/page.tsx` (change useAuth import)
- `frontend/src/components/ChatDrawer.tsx` (change useAuth import)
- `frontend/src/components/PipelineProgress.tsx` (rewrite for polling)
- `frontend/src/lib/types.ts` (remove run_id, PipelineMetadata)
- `frontend/package.json` (remove clerk + trigger packages)
- `frontend/.env.example` (remove Clerk/Trigger vars)

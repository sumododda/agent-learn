# Implementation Plan: Remove Trigger.dev and Clerk Dependencies

**Design spec**: `docs/superpowers/specs/2026-03-19-remove-trigger-clerk-design.md`
**Date**: 2026-03-19

---

## Phase 0: Allowed APIs (from documentation discovery)

### Python stdlib
- `asyncio.create_task(coro, *, name=None)` — must store reference in a `set()` to prevent GC
- `asyncio.gather(*aws, return_exceptions=True)` — for parallel research steps
- `import jwt` (PyJWT 2.x, already installed)
  - `jwt.encode(payload, key, algorithm="HS256") -> str`
  - `jwt.decode(token, key, algorithms=["HS256"]) -> dict` — `algorithms` is a **list**
  - Catch: `jwt.ExpiredSignatureError`, `jwt.InvalidTokenError`
- `from passlib.context import CryptContext` (must add `passlib[bcrypt]`)
  - `pwd_context.hash(secret) -> str`
  - `pwd_context.verify(secret, hash) -> bool`

### FastAPI
- Lifespan context manager (NOT `@app.on_event`, which is deprecated):
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      yield
      # shutdown logic here
  app = FastAPI(lifespan=lifespan)
  ```

### Tenacity (must add to requirements.txt)
- `from tenacity import retry, stop_after_attempt, wait_exponential`
- Works with `async def` natively (auto-detects and uses `asyncio.sleep`)

### Existing codebase patterns
- Session factory: `async_session()` from `backend/app/database.py:8`
- Agent functions: `run_discover_and_plan(course_id, session)`, `run_research_section(course_id, pos, session)`, etc. — all in `agent_service.py`
- Model pattern: `Mapped[]` + `mapped_column()` (SQLAlchemy 2.0), see `models.py`
- Config: `pydantic_settings.BaseSettings` with `.env` file
- Next.js 16 middleware: `src/proxy.ts` exporting `proxy` (not `middleware.ts`)

### Anti-patterns to avoid
- Do NOT use `python-jose` — `PyJWT` is already installed
- Do NOT use `@app.on_event("shutdown")` — deprecated, use lifespan
- Do NOT use `asyncio.TaskGroup` — cancels all on first error; use `gather(return_exceptions=True)`
- Do NOT drop `asyncio.Task` references — GC will silently cancel them

---

## Phase 1: Backend auth replacement

**Goal**: Replace Clerk JWT verification with local JWT auth. Add users table, register/login endpoints.

### Tasks

1. **Add `passlib[bcrypt]` to `backend/requirements.txt`**

2. **Add `User` model to `backend/app/models.py`**
   - Follow existing pattern (see `Course` model at line 15)
   - Fields: `id` (UUID PK, default uuid4), `email` (Text, unique, not null), `password_hash` (Text, not null), `created_at` (DateTime, server_default now)
   - Import the model in `__init__` or wherever Base.metadata picks it up

3. **Create Alembic migration**
   - `cd backend && alembic revision --autogenerate -m "create users table"`
   - Verify generated migration, then `alembic upgrade head`

4. **Update `backend/app/config.py`**
   - Remove: `CLERK_JWKS_URL`, `CLERK_ISSUER`
   - Add: `JWT_SECRET_KEY: str = ""`, `JWT_EXPIRE_MINUTES: int = 1440`

5. **Rewrite `backend/app/auth.py`**
   - Remove all Clerk JWKS fetching code (the `_get_jwks()` function, caching, httpx calls)
   - Keep `get_current_user` as `async def` with same signature: `(authorization: str | None = Header(default=None)) -> str`
   - New implementation: extract Bearer token, `jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])`, return `payload["sub"]`
   - Add helper: `def create_access_token(user_id: str) -> str` using `jwt.encode({"sub": user_id, "exp": ...}, settings.JWT_SECRET_KEY, algorithm="HS256")`
   - Add: `pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")`

6. **Create `backend/app/routers/auth_routes.py`**
   - `POST /api/auth/register`: accept `{email, password}`, check email uniqueness, hash password, insert user, return `{token, user_id}`
   - `POST /api/auth/login`: accept `{email, password}`, lookup user by email, verify password, return `{token, user_id}`
   - Use `Depends(get_session)` for DB access

7. **Register auth router in `backend/app/main.py`**
   - Add `from app.routers.auth_routes import router as auth_router`
   - Add `app.include_router(auth_router, prefix="/api/auth")`

8. **Add auth schemas to `backend/app/schemas.py`**
   - `RegisterRequest(email: str, password: str)`
   - `LoginRequest(email: str, password: str)`
   - `AuthResponse(token: str, user_id: str)`

9. **Update `backend/.env` and `backend/.env.example`**
   - Remove `CLERK_JWKS_URL`, `CLERK_ISSUER`
   - Add `JWT_SECRET_KEY=<generate-random-secret>`, `JWT_EXPIRE_MINUTES=1440`

### Verification
- [ ] `alembic upgrade head` succeeds
- [ ] `POST /api/auth/register` with `{email, password}` returns a JWT
- [ ] `POST /api/auth/login` with valid credentials returns a JWT
- [ ] `GET /api/courses` with the returned JWT in `Authorization: Bearer <token>` returns 200
- [ ] `GET /api/courses` with no token returns 401
- [ ] Duplicate email registration returns 409

---

## Phase 2: Frontend auth replacement

**Goal**: Replace Clerk components with custom AuthContext, login/register pages, and updated middleware.

### Tasks

1. **Create `frontend/src/context/AuthContext.tsx`**
   - `AuthProvider` component wrapping children
   - State: `token` (from localStorage), `user` (decoded from token or null), `isLoaded` (boolean)
   - Expose: `getToken(): Promise<string | null>` — check expiry, return token or null
   - Expose: `login(email, password): Promise<void>` — call `POST /api/auth/login`, store token
   - Expose: `register(email, password): Promise<void>` — call `POST /api/auth/register`, store token
   - Expose: `logout(): void` — clear token from localStorage
   - Expose: `isSignedIn: boolean`
   - On mount: read token from localStorage, check expiry, set `isLoaded = true`
   - Custom hook: `export function useAuth()` returning the context value

2. **Create `frontend/src/app/login/page.tsx`**
   - Simple form: email + password + submit
   - Call `login()` from AuthContext on submit
   - Redirect to `/` on success
   - Show error on failure

3. **Create `frontend/src/app/register/page.tsx`**
   - Simple form: email + password + submit
   - Call `register()` from AuthContext on submit
   - Redirect to `/` on success

4. **Rewrite `frontend/src/app/layout.tsx`**
   - Replace `ClerkProvider` import with `AuthProvider` from `@/context/AuthContext`
   - Replace `<ClerkProvider>` wrapper with `<AuthProvider>`
   - Replace `<Show when="signed-out"><SignInButton mode="modal">` with: if `!isSignedIn`, show a `<Link href="/login">Sign In</Link>`
   - Replace `<Show when="signed-in"><UserButton />` with: if `isSignedIn`, show user email + sign-out button

5. **Update all pages using `useAuth()`** — change import only:
   - `frontend/src/app/page.tsx` (L5): `import { useAuth } from '@/context/AuthContext'`
   - `frontend/src/app/library/page.tsx` (L5): same
   - `frontend/src/app/courses/[id]/page.tsx` (L5): same
   - `frontend/src/app/courses/[id]/learn/page.tsx` (L8): same
   - `frontend/src/components/ChatDrawer.tsx` (L4): same
   - No other changes needed — `getToken()` call pattern stays identical

6. **Rewrite `frontend/src/proxy.ts`**
   - Remove Clerk imports
   - Replace with a simple Next.js 16 middleware that:
     - Checks for auth token in cookies or headers
     - If no token and route is protected, redirect to `/login`
     - Public routes: `/`, `/login`, `/register`, `/api/auth/*`
   - Export `proxy` and `config` (Next.js 16 convention)

7. **Delete Clerk pages**
   - Delete `frontend/src/app/sign-in/` directory
   - Delete `frontend/src/app/sign-up/` directory

8. **Update `frontend/package.json`**
   - Remove `@clerk/nextjs`
   - Run `npm install` to update lockfile

9. **Update `frontend/.env.example`**
   - Remove: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL`

### Verification
- [ ] `npm run build` succeeds with no Clerk imports
- [ ] Navigate to `/login`, submit form, get redirected to `/`
- [ ] Navigate to `/register`, create account, get redirected to `/`
- [ ] Protected pages (`/library`, `/courses/*`) redirect to `/login` when not authenticated
- [ ] After login, all API calls include the JWT and return data
- [ ] Sign-out clears token and redirects to `/`

---

## Phase 3: Backend pipeline replacement

**Goal**: Replace Trigger.dev orchestration with asyncio background tasks.

### Tasks

1. **Add `tenacity` to `backend/requirements.txt`**

2. **Create `backend/app/pipeline.py`**
   - Import `asyncio`, `tenacity`, `async_session` from `database.py`, agent functions from `agent_service.py`
   - Define `PipelineStatus` dataclass: `stage`, `section`, `total`, `error`
   - Module-level: `_jobs: dict[str, PipelineStatus] = {}` and `_active_tasks: set[asyncio.Task] = set()`
   - `def get_pipeline_status(course_id: str) -> PipelineStatus | None`
   - `def update_status(course_id, stage, section=0, total=0, error=None)`
   - Retry wrappers using tenacity (match Trigger.dev retry counts):
     - `discover_and_plan`: 3 attempts, exponential backoff (multiplier=2, min=1, max=30)
     - `research_section`: 3 attempts
     - `verify_section`: 2 attempts
     - `write_section`: 3 attempts
     - `edit_section`: 2 attempts
   - `async def run_pipeline(course_id: str)`:
     1. Create session via `async with async_session() as session`, call `run_discover_and_plan`
     2. Get sections list, update status to "researching"
     3. Parallel research: `asyncio.gather(*[research_one(course_id, pos) for pos in positions], return_exceptions=True)` — each creates own session
     4. Sequential per section: verify → write → edit, each with own session
     5. Determine final status (completed/failed/completed_partial), update course via `update_course_status()`
     6. Clean up: remove from `_jobs` after a delay or keep for polling
   - `def start_pipeline(course_id: str) -> None`:
     - Create task: `task = asyncio.create_task(run_pipeline(course_id), name=f"pipeline-{course_id}")`
     - Store reference: `_active_tasks.add(task)` + `task.add_done_callback(_active_tasks.discard)`

3. **Update `backend/app/main.py`**
   - Add lifespan context manager for graceful shutdown:
     ```python
     @asynccontextmanager
     async def lifespan(app: FastAPI):
         yield
         # Cancel active pipeline tasks
         from app.pipeline import _active_tasks
         for task in _active_tasks:
             task.cancel()
         await asyncio.gather(*_active_tasks, return_exceptions=True)
     ```
   - Pass `lifespan=lifespan` to `FastAPI()`
   - Remove: `from app.routers.internal import router as internal_router` and its `include_router`

4. **Update `backend/app/routers/courses.py`**
   - In `POST /courses/{id}/generate`: replace the Trigger.dev HTTP call (lines ~145-162) with:
     ```python
     from app.pipeline import start_pipeline
     start_pipeline(str(course_id))
     ```
   - Remove `run_id` from the response
   - Add a pipeline status field to `GET /courses/{id}` response (read from `get_pipeline_status()`)

5. **Update `backend/app/schemas.py`**
   - Remove `run_id` from `GenerateResponse`
   - Remove all internal API schemas (~lines 124-228): `InternalCourseRequest`, `InternalSectionRequest`, `DiscoverAndPlanResponse`, etc.
   - Add `PipelineStatusResponse` schema if needed for the course detail endpoint

6. **Remove `backend/app/agent_service.py` legacy pipeline status**
   - Delete `_pipeline_status` dict (~line 49) and `update_pipeline_status()`, `get_pipeline_status()` functions (~lines 52-85)
   - These are superseded by `pipeline.py`

7. **Update `backend/app/config.py`**
   - Remove: `TRIGGER_SECRET_KEY`, `TRIGGER_API_URL`, `INTERNAL_API_TOKEN`

8. **Delete `backend/app/routers/internal.py`**

9. **Update `backend/.env` and `backend/.env.example`**
   - Remove: `TRIGGER_SECRET_KEY`, `TRIGGER_API_URL`, `INTERNAL_API_TOKEN`

### Verification
- [ ] `POST /api/courses/{id}/generate` returns 200 and starts pipeline in background
- [ ] `GET /api/courses/{id}` shows pipeline progress (stage, section, total)
- [ ] Pipeline completes successfully — course status becomes "completed"
- [ ] If a section fails, pipeline continues and sets "completed_partial"
- [ ] No imports from `trigger` or references to `TRIGGER_*` remain in backend (grep check)

---

## Phase 4: Frontend pipeline cleanup

**Goal**: Remove all Trigger.dev imports, rewrite PipelineProgress for polling.

### Tasks

1. **Rewrite `frontend/src/components/PipelineProgress.tsx`**
   - Remove `import { useRealtimeRun } from '@trigger.dev/react-hooks'`
   - Remove all `useRealtimeRun` usage
   - Component now receives `courseId` and polls `GET /api/courses/{courseId}` every 3-5 seconds
   - Display progress from the pipeline status in the course response
   - Call `onComplete` callback when status is "completed"

2. **Update `frontend/src/app/courses/[id]/page.tsx`**
   - Remove `runId` state variable
   - Remove `NEXT_PUBLIC_TRIGGER_PUBLIC_API_KEY` reference
   - Remove Trigger.dev realtime branch — keep only the polling logic
   - Simplify `PipelineProgress` props (no more `runId`, `accessToken`)

3. **Update `frontend/src/lib/types.ts`**
   - Remove `run_id` from `GenerateResponse`
   - Remove `PipelineMetadata` interface
   - Update `PipelineStatus` to match new backend shape

4. **Update `frontend/package.json`**
   - Remove `@trigger.dev/react-hooks`
   - Run `npm install` to update lockfile

5. **Update `frontend/.env.example`**
   - Remove `NEXT_PUBLIC_TRIGGER_PUBLIC_API_KEY`

### Verification
- [ ] `npm run build` succeeds with no Trigger.dev imports
- [ ] Grep: no `@trigger.dev` references in `frontend/src/`
- [ ] Course generation shows progress in UI via polling
- [ ] Pipeline completion navigates to learn page

---

## Phase 5: Cleanup and deletion

**Goal**: Remove all dead code, files, and directories.

### Tasks

1. **Delete entire `trigger/` directory**
   - This removes all Trigger.dev task files, config, package.json, node_modules, .trigger/

2. **Delete test files**
   - Delete `backend/tests/test_internal_api.py`
   - Rewrite `backend/tests/test_auth.py` for new JWT auth flow (register, login, token validation)

3. **Update `docker-compose.yml`** if it references any Trigger.dev services

4. **Final grep checks**
   - `grep -r "trigger" backend/` — should find nothing relevant
   - `grep -r "clerk" frontend/src/` — should find nothing
   - `grep -r "@trigger.dev" frontend/` — should find nothing
   - `grep -r "CLERK_" backend/` — should find nothing
   - `grep -r "INTERNAL_API_TOKEN" backend/` — should find nothing

5. **Wipe Clerk user data from DB** (if desired)
   - Existing `user_id` values are Clerk IDs. Either leave them or truncate dev data.

### Verification
- [ ] Backend starts without errors: `uvicorn app.main:app`
- [ ] Frontend builds without errors: `npm run build`
- [ ] Full flow works: register → create course → generate → view progress → read content → chat
- [ ] All grep checks pass (no stale references)

---

## Phase ordering and dependencies

```
Phase 1 (backend auth) → Phase 2 (frontend auth)
Phase 3 (backend pipeline) → Phase 4 (frontend pipeline)
Phase 5 (cleanup) depends on all above

Phases 1-2 and 3-4 are independent tracks — can be done in either order.
Recommended: auth first (1→2), then pipeline (3→4), then cleanup (5).
```

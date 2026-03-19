# Milestone 3 — Implementation Plan

**Design spec:** `docs/superpowers/specs/2026-03-18-milestone-3-design.md`

## Phase 0: Documentation Discovery (Reference)

### Trigger.dev v3 API Reference

- **Task definition:** `task({ id, retry: { maxAttempts, factor, minTimeoutInMs, maxTimeoutInMs }, run: async (payload, { ctx }) => {} })`
- **triggerAndWait:** `const result = await childTask.triggerAndWait(payload)` — returns `Result<T>` with `.ok`, `.output`, `.error`. Does NOT throw on child failure. Use `.unwrap()` to convert to exception.
- **batchTriggerAndWait:** `batch.triggerAndWait<typeof task>([{ id, payload }])` — returns `{ runs: Array<Result> }`
- **Metadata:** `metadata.set(key, value)`, `metadata.parent.set(key, value)` — JSON-serializable values, real-time updates
- **React hooks:** `useRealtimeRun(runId, { accessToken })` returns `{ run, error, isLoading }`. `run.metadata` has live metadata.
- **REST trigger (from Python):** `POST https://api.trigger.dev/api/v1/tasks/{taskId}/trigger` with `Authorization: Bearer <TRIGGER_SECRET_KEY>`, body `{ payload, options }`
- **Public tokens:** `auth.createPublicToken({ scopes: { runs: { read: [runId] } } })` — 15 min expiry
- **Config:** `trigger.config.ts` with `defineConfig({ project, dirs: ["./src/trigger"], retries: { enabledInDev: false, default: { maxAttempts: 3 } } })`
- **Dev mode:** `npx trigger.dev@latest dev`
- **Anti-pattern:** Do NOT use `Promise.all()` with triggerAndWait — use `batchTriggerAndWait` instead

### Clerk API Reference

- **Next.js setup:** `@clerk/nextjs`, wrap layout in `<ClerkProvider>`, create `middleware.ts` with `clerkMiddleware()` + `createRouteMatcher()`
- **Env vars:** `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL`
- **Server-side userId:** `const { userId } = await auth()` from `@clerk/nextjs/server`
- **Client-side JWT:** `const { getToken } = useAuth()` then `const token = await getToken()`
- **Python verification:** PyJWT + JWKS endpoint `https://<clerk-domain>/.well-known/jwks.json`, decode with RS256, extract `sub` as userId
- **Components:** `<SignInButton>`, `<SignUpButton>`, `<UserButton>`, `<Show when="signed-in">`

### Codebase Reference

- **Pipeline orchestrator:** `backend/app/agent_service.py` — `generate_lessons()` is the main function to decompose
- **Agent factories:** `backend/app/agent.py` — `create_planner()`, `create_writer()`, plus M2 agents (discovery researcher, section researcher, verifier, editor)
- **Router:** `backend/app/routers/courses.py` — `POST /api/courses/{id}/generate` currently calls `generate_lessons()` synchronously
- **Models:** `backend/app/models.py` — Course (status field: text), Section, ResearchBrief, EvidenceCard, Blackboard
- **Frontend API:** `frontend/src/lib/api.ts` — `generateCourse(id)` currently blocks until complete
- **Status values in use:** `outline_ready`, `generating`, `completed`, `failed`

---

## Phase 1: Trigger.dev Project Setup

### What to implement

1. **Initialize Trigger.dev project** in `trigger/` directory
   - Create `trigger/package.json` with `@trigger.dev/sdk` dependency
   - Create `trigger/tsconfig.json`
   - Create `trigger/trigger.config.ts` with project ref, task dirs, retry defaults
   - Create `trigger/src/lib/api-client.ts` — typed HTTP client that calls Python backend internal endpoints

2. **Add environment variables**
   - `TRIGGER_SECRET_KEY` — for Trigger.dev authentication
   - `INTERNAL_API_URL` — Python backend URL (e.g., `http://localhost:8000`)
   - `INTERNAL_API_TOKEN` — shared secret for internal endpoint auth
   - Add to `backend/.env.example` and `trigger/.env.example`

3. **Create a smoke-test task** to verify Trigger.dev runs locally
   - `trigger/src/tasks/hello.ts` — simple task that returns a string
   - Verify `npx trigger.dev@latest dev` starts and the task executes

### Documentation references

- Config: `defineConfig({ project, dirs, retries })` from Trigger.dev docs
- Dev mode: `npx trigger.dev@latest dev`

### Verification checklist

- [ ] `cd trigger && npm install` succeeds
- [ ] `npx trigger.dev@latest dev` starts without errors
- [ ] Smoke-test task can be triggered and returns result
- [ ] `api-client.ts` compiles (TypeScript check)

### Anti-pattern guards

- Do NOT add `@trigger.dev/react-hooks` to `trigger/` — that goes in `frontend/` (Phase 4)
- Do NOT put task files outside `trigger/src/tasks/` — config `dirs` must match

---

## Phase 2: Internal API Endpoints (Python)

### What to implement

1. **Add internal router** at `backend/app/routers/internal.py`
   - FastAPI dependency `verify_internal_token()` that checks `X-Internal-Token` header against `INTERNAL_API_TOKEN` env var
   - All endpoints in this router require the dependency

2. **Create 5 internal endpoints** by wrapping existing `agent_service.py` functions:

   | Endpoint | Wraps | Input | Output |
   |---|---|---|---|
   | `POST /api/internal/discover-and-plan` | `run_discovery_research()` + `run_planner()` | `{ course_id }` | `{ sections, research_briefs }` |
   | `POST /api/internal/research-section` | `research_section()` | `{ course_id, section_position }` | `{ evidence_cards }` |
   | `POST /api/internal/verify-section` | `verify_evidence()` | `{ course_id, section_position }` | `{ verification_result }` |
   | `POST /api/internal/write-section` | `write_section()` | `{ course_id, section_position }` | `{ content, citations }` |
   | `POST /api/internal/edit-section` | `edit_section()` | `{ course_id, section_position }` | `{ edited_content, blackboard_updates }` |

3. **Refactor `agent_service.py`** — extract the per-stage logic from `generate_lessons()` into standalone async functions if not already separated. Each function should:
   - Accept a db session + stage-specific input
   - Read what it needs from DB
   - Do LLM/search work
   - Write results to DB
   - Return a serializable response

4. **Add `INTERNAL_API_TOKEN`** to `backend/app/config.py` Settings class

5. **Add Pydantic schemas** for internal endpoint request/response in `backend/app/schemas.py`

### Documentation references

- Existing patterns: `backend/app/routers/courses.py` for router structure
- Existing patterns: `backend/app/schemas.py` for Pydantic models

### Verification checklist

- [ ] Each internal endpoint returns correct response when called with valid token
- [ ] Each internal endpoint returns 401 when called without token
- [ ] Each internal endpoint returns 401 when called with wrong token
- [ ] Existing M2 tests still pass (no regressions)
- [ ] New unit tests for each internal endpoint (mocked LLM/Tavily)

### Anti-pattern guards

- Do NOT delete `generate_lessons()` yet — keep it until Trigger.dev pipeline is wired
- Do NOT expose internal endpoints without the token check
- Do NOT change public endpoint behavior — they stay as-is until Phase 5

---

## Phase 3: Trigger.dev Pipeline Tasks

### What to implement

1. **Create pipeline tasks** in `trigger/src/tasks/`:

   **`discover-and-plan.ts`:**
   - Calls `POST /api/internal/discover-and-plan` via api-client
   - Retry: `maxAttempts: 3`
   - Returns section list + research briefs

   **`research-section.ts`:**
   - Calls `POST /api/internal/research-section` via api-client
   - Retry: `maxAttempts: 3`
   - Returns evidence cards for one section

   **`verify-section.ts`:**
   - Calls `POST /api/internal/verify-section` via api-client
   - Retry: `maxAttempts: 2`

   **`write-section.ts`:**
   - Calls `POST /api/internal/write-section` via api-client
   - Retry: `maxAttempts: 3`

   **`edit-section.ts`:**
   - Calls `POST /api/internal/edit-section` via api-client
   - Retry: `maxAttempts: 2`

   **`generate-course.ts` (parent orchestrator):**
   - Accepts `{ courseId }` as payload
   - Calls `discoverAndPlan` via `triggerAndWait`
   - Updates `metadata.set("pipeline", { status, sections })` after each stage
   - Calls `researchSection` for all sections via `batchTriggerAndWait` (parallel)
   - For each section sequentially: verify → write → edit, each via `triggerAndWait`
   - On child failure (`.ok === false`): mark section as failed in metadata, continue with next section
   - At end: call Python endpoint to set final course status (`completed` or `completed_partial`)

2. **Update `api-client.ts`** with typed functions for each internal endpoint

### Documentation references

- `triggerAndWait` returns `Result<T>` — check `.ok` before accessing `.output`
- `batchTriggerAndWait` — `batch.triggerAndWait<typeof task>([{ id, payload }])`
- `metadata.set()` for progress updates
- Do NOT use `Promise.all()` with `triggerAndWait`

### Verification checklist

- [ ] Each task compiles (TypeScript check)
- [ ] `generate-course` task orchestrates the full pipeline when triggered manually
- [ ] Metadata updates are visible during execution (check Trigger.dev dashboard or logs)
- [ ] If one section's research fails, the parent continues with other sections
- [ ] If one section's write/edit fails, that section is marked failed but others complete

### Anti-pattern guards

- Do NOT use `Promise.all()` for parallel research — use `batchTriggerAndWait`
- Do NOT retry the parent task — only child tasks retry
- Do NOT call `.unwrap()` on child results in the parent — use `.ok` check for graceful degradation

---

## Phase 4: Wire Frontend to Trigger.dev Realtime

### What to implement

1. **Update `POST /api/courses/{id}/generate` endpoint** in Python:
   - Instead of calling `generate_lessons()` or BackgroundTasks, call Trigger.dev REST API: `POST https://api.trigger.dev/api/v1/tasks/generate-course/trigger`
   - Pass `{ payload: { courseId } }` with `Authorization: Bearer <TRIGGER_SECRET_KEY>`
   - Response includes `run_id`
   - Generate a public access token (call Trigger.dev API or generate in the Trigger.dev task and return it)
   - Return `{ run_id, public_access_token }` to frontend

2. **Update `GenerateResponse` schema** to include `run_id` and `public_access_token` fields

3. **Add `@trigger.dev/react-hooks`** to `frontend/package.json`

4. **Update frontend generate flow:**
   - `generateCourse()` in `api.ts` returns `{ run_id, public_access_token }` instead of full Course
   - Course detail page (`courses/[id]/page.tsx`): after approve, receive `run_id` + token, pass to `PipelineProgress`
   - `PipelineProgress` component: replace polling with `useRealtimeRun(runId, { accessToken })`, read `run.metadata.pipeline` for per-section status

5. **Remove polling infrastructure:**
   - Remove `GET /api/courses/{id}/pipeline-status` endpoint (if it exists on main)
   - Remove `_pipeline_status` dict from `agent_service.py` (if it exists on main)
   - Remove polling `setInterval` from frontend components

### Documentation references

- React hook: `useRealtimeRun(runId, { accessToken, enabled: !!runId })`
- `run.metadata` contains live metadata set by tasks
- REST trigger: `POST /api/v1/tasks/{taskId}/trigger` with Bearer auth

### Verification checklist

- [ ] `POST /api/courses/{id}/generate` returns `run_id` and `public_access_token`
- [ ] Frontend `PipelineProgress` shows live per-section status updates
- [ ] No polling network requests visible in browser DevTools during generation
- [ ] Completed sections are navigable while generation continues
- [ ] Frontend handles generation failure gracefully (shows error state)

### Anti-pattern guards

- Do NOT expose `TRIGGER_SECRET_KEY` to the frontend — only `public_access_token`
- Do NOT keep the old polling code as a fallback — clean removal

---

## Phase 5: Auth (Clerk)

### What to implement

1. **Clerk Next.js setup:**
   - `npm install @clerk/nextjs` in `frontend/`
   - Add env vars: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up`
   - Wrap root layout (`app/layout.tsx`) with `<ClerkProvider>`
   - Create `frontend/middleware.ts` with `clerkMiddleware()` — protect all routes except `/`, `/sign-in(.*)`, `/sign-up(.*)`
   - Create `/sign-in` and `/sign-up` pages using Clerk's `<SignIn>` and `<SignUp>` components

2. **Frontend API auth:**
   - Update `api.ts` to accept and send `Authorization: Bearer <token>` header
   - Create a hook or utility that uses `useAuth().getToken()` to get the JWT
   - All API calls include the Clerk JWT

3. **Python backend auth:**
   - `pip install PyJWT cryptography requests` (add to `requirements.txt`)
   - Add `CLERK_JWKS_URL` and `CLERK_ISSUER` to `backend/app/config.py`
   - Create `backend/app/auth.py` — FastAPI dependency `get_current_user()` that:
     - Extracts Bearer token from Authorization header
     - Fetches JWKS from Clerk (with caching)
     - Verifies JWT with RS256
     - Returns `user_id` (from `sub` claim)
   - Add `get_current_user` dependency to all public course endpoints
   - Internal endpoints continue using `X-Internal-Token` (no Clerk JWT)

4. **Database changes:**
   - Add `user_id: Mapped[str | None]` column to Course model
   - Alembic migration: add nullable `user_id` column to courses
   - Update `POST /api/courses` to set `user_id` from authenticated user
   - Update `GET /api/courses` to filter by `user_id`
   - Update `GET /api/courses/{id}` to verify user owns the course (403 if not)

5. **Add header with auth UI:**
   - Add `<SignInButton>`, `<UserButton>`, `<Show>` components to layout or header

### Documentation references

- Middleware: `clerkMiddleware()` + `createRouteMatcher()` from `@clerk/nextjs/server`
- Server auth: `const { userId } = await auth()` from `@clerk/nextjs/server`
- Client JWT: `const { getToken } = useAuth()` from `@clerk/nextjs`
- Python: `jwt.decode(token, key, algorithms=["RS256"])` — `sub` claim = userId

### Verification checklist

- [ ] Unauthenticated users redirected to sign-in page
- [ ] Signed-in users can access all app routes
- [ ] API calls include Clerk JWT in Authorization header
- [ ] Python backend rejects requests without valid JWT (401)
- [ ] Users only see their own courses
- [ ] Internal endpoints still work with X-Internal-Token (not affected by Clerk)
- [ ] Existing tests updated to pass auth headers (or test auth separately)

### Anti-pattern guards

- Do NOT store Clerk secret key in frontend code
- Do NOT skip JWKS caching — Clerk rate-limits the JWKS endpoint
- Do NOT make `user_id` column non-nullable in the migration (breaks existing data)

---

## Phase 6: Learner Progress & Resume

### What to implement

1. **Database:**
   - Create `LearnerProgress` model in `models.py`:
     - `id` (UUID PK), `user_id` (TEXT), `course_id` (UUID FK), `current_section` (INT), `completed_sections` (JSON), `last_accessed_at` (TIMESTAMP), `created_at` (TIMESTAMP)
     - Unique constraint on `(user_id, course_id)`
   - Alembic migration

2. **Backend endpoints:**
   - `POST /api/courses/{id}/progress` — upsert: update `current_section`, optionally add to `completed_sections`, update `last_accessed_at`
   - `GET /api/me/courses` — list user's courses with progress data, sorted by `last_accessed_at` desc

3. **Update `CourseResponse` schema** to include optional progress fields (`current_section`, `completed_sections`, `last_accessed_at`) when authenticated

4. **Frontend:**
   - When learner navigates to a section: call `POST /api/courses/{id}/progress` with `{ current_section: position }`
   - When learner clicks "Next": call with `{ current_section: nextPosition, completed_section: currentPosition }`
   - Library page: use `GET /api/me/courses`, show progress bar per course, sort by recency
   - Course learn page: on initial load, jump to `current_section` instead of section 0
   - Sidebar: show checkmark on completed sections

### Documentation references

- Existing patterns: `backend/app/models.py` for SQLAlchemy model structure
- Existing patterns: `backend/app/routers/courses.py` for endpoint structure
- Existing patterns: `frontend/src/app/courses/[id]/learn/page.tsx` for section navigation

### Verification checklist

- [ ] Progress persists across page reloads
- [ ] Returning to a course lands on the last viewed section
- [ ] Completed sections show checkmarks in sidebar
- [ ] Library page shows progress per course
- [ ] Library page sorted by last accessed
- [ ] Progress is per-user (user A's progress doesn't affect user B)

### Anti-pattern guards

- Do NOT track scroll position or reading time — section-level only
- Do NOT create progress records for unauthenticated users

---

## Phase 7: Integration Testing & Verification

### What to implement

1. **Trigger.dev pipeline integration test:**
   - Start Python backend with mocked LLM
   - Trigger `generateCourse` task
   - Verify all stages execute in order
   - Verify metadata updates at each stage
   - Verify course status is `completed` at end
   - Verify partial failure: mock one section's write to fail, verify `completed_partial`

2. **Auth integration test:**
   - Verify unauthenticated requests get 401
   - Verify users only see their own courses
   - Verify internal endpoints reject Clerk JWTs and accept internal tokens

3. **Progress integration test:**
   - Create course, update progress, verify resume behavior

4. **Frontend E2E smoke test (manual):**
   - Sign in → create course → approve outline → watch realtime progress → read sections → sign out → sign in → resume course

5. **Regression verification:**
   - All existing M2 tests pass
   - No broken imports or missing dependencies

### Verification checklist

- [ ] Full pipeline runs end-to-end with mocked LLM responses
- [ ] Partial failures handled gracefully (some sections complete, some fail)
- [ ] Auth protects all endpoints correctly
- [ ] Progress persists and resume works
- [ ] No regressions in existing test suite
- [ ] `grep -r "_pipeline_status" backend/` returns no results (removed)
- [ ] `grep -r "setInterval" frontend/src/` returns no polling for pipeline status

### Anti-pattern guards

- Do NOT skip testing partial failure scenarios — this is the most important resilience feature
- Do NOT assume Trigger.dev dev mode behaves identically to production — test both

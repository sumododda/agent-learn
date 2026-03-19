# Milestone 3 — Production Delivery

## Goal

Make agent-learn work at real-world speed and reliability. A learner can start reading while generation continues, resume unfinished courses across sessions, and access their courses behind authentication.

**Proves:** a learner can start reading while generation continues.

## Scope

Five features from `milestones.md`:

1. Background generation (Trigger.dev)
2. Progressive delivery via realtime (Trigger.dev React hooks, replacing polling)
3. Auth (Clerk)
4. Persist learner progress and support resuming unfinished courses
5. Checkpointing and retry on failures

## Current State (post-M2)

- Pipeline: discovery research → evidence cards → verification → writing → editing → blackboard
- Background: FastAPI `BackgroundTasks` (local, not durable, lost on restart)
- Progress: in-memory `_pipeline_status` dict, frontend polls every 3s
- Auth: none
- Progress tracking: none
- Error handling: per-section isolation (write/edit failures skip section, don't halt pipeline)
- Tests: 108 passing

## Architecture Decision: Trigger.dev as Orchestrator

Trigger.dev (TypeScript) becomes the pipeline orchestrator. Python backend becomes a set of stateless internal endpoints that do LLM/DB work.

**Why this split:**

- Trigger.dev provides durable execution, automatic retries, checkpointing between child tasks, and realtime metadata streaming — all out of the box
- The actual LLM calls, Tavily searches, and DB operations stay in Python where the existing code lives
- Trigger.dev tasks are thin orchestration wrappers that call Python endpoints and pass results forward
- React hooks (`useRealtimeRun`) replace polling for progress updates

**Trade-off:** Orchestration logic splits across two languages (TypeScript + Python). Accepted because it uses Trigger.dev the way it's designed and avoids reimplementing durability primitives.

## 1. Trigger.dev Pipeline Architecture

### Task Hierarchy

```
generateCourse (parent task)
├── discoverAndPlan        → POST /api/internal/discover-and-plan
├── researchSections       → batchTriggerAndWait (parallel)
│   ├── researchSection[0] → POST /api/internal/research-section
│   ├── researchSection[1] → ...
│   └── researchSection[N] → ...
├── for each section (sequential):
│   ├── verifySection      → POST /api/internal/verify-section
│   ├── writeSection       → POST /api/internal/write-section
│   └── editSection        → POST /api/internal/edit-section
```

**Parallelism model:**

- Research is independent per section → `batchTriggerAndWait` for parallel fan-out
- Verify → Write → Edit must be sequential per section (blackboard accumulates across sections)
- Each child task gets Trigger.dev's automatic retries and checkpointing
- Parent task updates `metadata` at each stage transition → frontend gets realtime progress

### New Project Structure

```
trigger/
├── src/
│   ├── tasks/
│   │   ├── generate-course.ts    # Parent orchestrator
│   │   ├── discover-and-plan.ts
│   │   ├── research-section.ts
│   │   ├── verify-section.ts
│   │   ├── write-section.ts
│   │   └── edit-section.ts
│   └── lib/
│       └── api-client.ts         # Typed HTTP client for Python backend
├── trigger.config.ts
├── package.json
└── tsconfig.json
```

### Parent Task Behavior (`generate-course.ts`)

1. Call `discoverAndPlan` via `triggerAndWait` → receives section list
2. Update metadata: all sections in `researching` state
3. Call `researchSection` for all sections via `batchTriggerAndWait` (parallel)
4. For each section sequentially:
   - Update metadata: section entering `verifying`
   - Call `verifySection` via `triggerAndWait`
   - Update metadata: section entering `writing`
   - Call `writeSection` via `triggerAndWait`
   - Update metadata: section entering `editing`
   - Call `editSection` via `triggerAndWait`
   - Update metadata: section `completed`
5. Update course status to `completed` (or `completed_partial` if any sections failed)

Checkpointing happens automatically between each `triggerAndWait` / `batchTriggerAndWait` call. If the worker crashes, it resumes from the last completed child task.

## 2. Internal API Layer

New stateless endpoints that Trigger.dev tasks call. Each reads from DB, does LLM/DB work, writes results to DB, returns a response.

### Endpoints

| Endpoint | Input | Output | Wraps |
|---|---|---|---|
| `POST /api/internal/discover-and-plan` | `{course_id}` | `{sections, research_briefs}` | `run_discovery_research()` + `run_planner()` |
| `POST /api/internal/research-section` | `{course_id, section_position}` | `{evidence_cards[]}` | `research_section()` |
| `POST /api/internal/verify-section` | `{course_id, section_position}` | `{verification_result}` | `verify_section()` |
| `POST /api/internal/write-section` | `{course_id, section_position}` | `{content, citations}` | `write_section()` |
| `POST /api/internal/edit-section` | `{course_id, section_position}` | `{edited_content, blackboard_updates}` | `edit_section()` |

### Security

Internal endpoints are protected by a shared secret header (`X-Internal-Token`). The token is set in both the Trigger.dev worker environment and the Python backend environment. A FastAPI dependency checks the header and rejects requests without a valid token.

### Changes to `agent_service.py`

- Break `generate_course_content()` into individual stage functions
- Remove the orchestration loop (moves to Trigger.dev)
- Remove `_pipeline_status` in-memory dict (replaced by Trigger.dev metadata)
- Each function: read from DB → do work → write to DB → return response

### Changes to Public Endpoints

- `POST /api/courses/{id}/generate` changes from running `BackgroundTasks` to triggering the Trigger.dev `generateCourse` task via HTTP API, returning `{run_id, public_access_token}`
- `GET /api/courses/{id}/pipeline-status` is removed (replaced by Trigger.dev realtime)
- All other public endpoints remain unchanged

## 3. Realtime Progress

Replaces the current polling-based progress system.

### Flow

1. `POST /api/courses/{id}/generate` triggers Trigger.dev task, returns `{run_id, public_access_token}`
2. Frontend receives both in the API response
3. Frontend uses `useRealtimeRun` React hook to subscribe to live metadata updates
4. Trigger.dev parent task updates `metadata` at each stage transition

### Metadata Shape

```typescript
{
  pipeline: {
    status: "researching" | "writing" | "completed" | "completed_partial" | "failed",
    sections: {
      [position: number]: {
        stage: "pending" | "researching" | "verifying" | "writing" | "editing" | "completed" | "failed",
        error?: string
      }
    }
  }
}
```

### Frontend Changes

- `PipelineProgress` component rewired: remove polling, use `useRealtimeRun` hook
- Add `@trigger.dev/react-hooks` package to frontend
- Course detail page receives `runId` + `publicAccessToken` from generate response, passes to `PipelineProgress`

### Removals

- `_pipeline_status` dict in `agent_service.py`
- `GET /api/courses/{id}/pipeline-status` endpoint
- 3-second polling interval in frontend
- Polling logic in `PipelineProgress` component

## 4. Auth (Clerk)

### Why Clerk

- Database is local PostgreSQL, not Supabase — no ecosystem advantage from Supabase Auth
- Excellent Next.js integration (`@clerk/nextjs`) — middleware, `auth()` helper, pre-built components
- Sign-in/sign-up UI out of the box
- Simple to add incrementally without database migration

### Frontend

- Add `@clerk/nextjs` package
- Wrap app in `<ClerkProvider>` in root layout
- Clerk middleware protects all routes except `/` (landing), `/sign-in`, `/sign-up`
- `auth()` provides `userId` in server components and API route handlers
- Frontend sends Clerk JWT in `Authorization: Bearer <token>` header on all API calls

### Backend

- Add `clerk` Python package for JWT verification
- New FastAPI dependency that verifies Clerk JWT from `Authorization` header
- Extract `user_id` from verified token, inject into route handlers
- Internal endpoints use `X-Internal-Token` instead (Trigger.dev calls carry no user context; `user_id` is read from the course record in DB)

### Database

- Add `user_id TEXT` column to `courses` table
- All course queries filter by `user_id`
- Alembic migration: add column with nullable default, then backfill existing dev data

### Scope Boundaries

- No roles or permissions — every user owns their own courses
- No team or sharing features
- No social login beyond Clerk defaults (Google, GitHub available out of the box)

## 5. Learner Progress & Resume

### New Table: `learner_progress`

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | TEXT | Clerk user ID |
| `course_id` | UUID FK → courses | |
| `current_section` | INT | Last section the learner was viewing |
| `completed_sections` | JSON | Array of completed section positions |
| `last_accessed_at` | TIMESTAMP | For sorting by recency |
| `created_at` | TIMESTAMP | |

Unique constraint on `(user_id, course_id)`.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/courses/{id}/progress` | Update current section and/or mark section completed |
| `GET /api/me/courses` | List authenticated user's courses with progress, sorted by `last_accessed_at` |

### Behavior

- Navigating to a section → updates `current_section` + `last_accessed_at`
- Clicking "Next" at end of section → adds section position to `completed_sections`
- `GET /api/courses/{id}` response includes progress data for the authenticated user
- Library page shows per-course progress (e.g., "3/7 sections completed")
- Returning to a course lands on `current_section`, not section 0

### Scope Boundaries

- Section-level granularity only (no scroll position, reading time, or bookmarks)
- Progress is per-user-per-course
- No cross-course analytics

## 6. Checkpointing & Retry

### Trigger.dev Built-ins

- Automatic checkpointing between `triggerAndWait` / `batchTriggerAndWait` calls
- If worker crashes, resumes from last completed child task
- Automatic retries with configurable exponential backoff per task

### Retry Configuration

| Task | Max Attempts | Rationale |
|---|---|---|
| `generateCourse` (parent) | 1 | No retry — child retries handle transient failures |
| `discoverAndPlan` | 3 | LLM call can flake; outline is cheap to regenerate |
| `researchSection` | 3 | Tavily can timeout; evidence is independent per section |
| `verifySection` | 2 | Pure LLM judgment; if it fails twice, something is wrong |
| `writeSection` | 3 | LLM call; worth retrying on transient failures |
| `editSection` | 2 | Similar to verify |

### Failure Handling

- When a section task exhausts retries: mark that section as `failed` in DB, parent continues with remaining sections
- Course status becomes `completed_partial` if any sections failed, `failed` if `discoverAndPlan` fails
- Frontend shows failed sections with a "Retry" button that re-triggers just that section's sub-pipeline
- Verification re-research retry stays — that's domain logic (verifier says "not enough evidence"), not infrastructure retry

### New Course Statuses

Add to existing status enum:

- `completed_partial` — generation finished but some sections failed
- `failed` — entire pipeline failed (e.g., discover-and-plan exhausted retries)

## Database Migrations Summary

One Alembic migration covering:

1. Add `user_id TEXT` column to `courses` (nullable, backfill dev data)
2. Add `completed_partial` and `failed` to course status options
3. Create `learner_progress` table with unique constraint on `(user_id, course_id)`

## New Dependencies

### Frontend (`frontend/package.json`)

- `@clerk/nextjs` — auth provider, middleware, components
- `@trigger.dev/react-hooks` — `useRealtimeRun`, `useTaskTrigger`

### Backend (`backend/requirements.txt`)

- `clerk` — JWT verification

### New Package (`trigger/package.json`)

- `@trigger.dev/sdk` — task definitions, metadata, triggering
- TypeScript, Node.js runtime

## Testing Strategy

### Trigger.dev Tasks

- Unit tests with mocked HTTP calls to Python backend
- Integration test: trigger `generateCourse` against running Python backend with mocked LLM responses

### Internal API Endpoints

- Same mocking strategy as M2 tests (mocked LLM, mocked Tavily)
- Test each endpoint independently: given DB state → call endpoint → verify DB state + response

### Auth

- Test Clerk middleware rejects unauthenticated requests
- Test `user_id` filtering on course queries
- Test internal endpoints accept `X-Internal-Token` and reject Clerk JWTs

### Learner Progress

- Test progress update and retrieval
- Test resume flow (current_section is returned correctly)
- Test library listing with progress data and sort order

### Existing Tests

- M2 tests continue to pass (internal endpoint functions are the same code, just exposed via new routes)
- `PipelineProgress` component tests updated for realtime hook instead of polling

## What Gets Removed

- `_pipeline_status` in-memory dict in `agent_service.py`
- `GET /api/courses/{id}/pipeline-status` endpoint
- Polling logic in `PipelineProgress` component
- `BackgroundTasks` usage in `POST /api/courses/{id}/generate`
- Orchestration loop in `agent_service.py` (replaced by Trigger.dev tasks)

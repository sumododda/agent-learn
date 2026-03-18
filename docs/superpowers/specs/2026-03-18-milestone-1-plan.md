# Milestone 1 — Implementation Plan

**Design spec:** `docs/superpowers/specs/2026-03-18-milestone-1-design.md`
**Created:** 2026-03-18

---

## Phase 0: Documentation Discovery (Reference)

Findings from doc discovery subagents. All implementation phases reference these patterns.

### Allowed APIs — Deep Agents

| API | Import | Notes |
|---|---|---|
| `create_deep_agent()` | `from deepagents import create_deep_agent` | Returns `CompiledStateGraph`. Accepts `model`, `tools`, `system_prompt`, `subagents`, `response_format`, `backend`, `checkpointer`, `store`, `name` |
| Subagent config | Dict with `name`, `description`, `system_prompt`, `tools` keys | Optional: `model` (override parent model) |
| `init_chat_model()` | `from langchain.chat_models import init_chat_model` | For OpenRouter: `model_provider="openai"`, `base_url="https://openrouter.ai/api/v1"`, `api_key=OPENROUTER_API_KEY` |
| `ToolStrategy` | `from langchain.agents.structured_output import ToolStrategy` | Pass Pydantic model: `response_format=ToolStrategy(MyModel)`. Result in `["structured_response"]` |
| `task()` tool | Auto-wired when `subagents` are defined | Supervisor delegates via built-in `task()` tool; subagents run in isolated context |
| `FilesystemBackend` | `from deepagents.backends import FilesystemBackend` | `root_dir`, `virtual_mode` params. Default is `StateBackend` (in-memory) |

**Anti-patterns:**
- Do NOT pass a raw string model name for OpenRouter — must use `init_chat_model()` with `base_url`
- Do NOT assume subagents share context with the supervisor — they are isolated
- The exact pip package name needs verification at install time (`deepagents` vs `langchain-deepagents`)

### Allowed APIs — Next.js App Router

| API | Import/Path | Notes |
|---|---|---|
| Dynamic route params | `params: Promise<{ id: string }>` | Must `await params` — this is a recent change |
| `useRouter` | `from 'next/navigation'` | NOT `'next/router'` in App Router |
| `Link` | `from 'next/link'` | Standard navigation |
| `loading.tsx` | File convention | Auto-wrapped in Suspense |
| Server components | Default (no directive) | Fetch data with `async/await` directly |
| Client components | `'use client'` directive | For form state, interactivity |
| Tailwind | `npx create-next-app@latest` with Tailwind option | Auto-scaffolds config + globals.css |
| Markdown rendering | `react-markdown` + `@tailwindcss/typography` | Wrap in `<div className="prose">` |

**Anti-patterns:**
- Do NOT import `useRouter` from `'next/router'` — that's Pages Router
- Do NOT access `params.id` synchronously — must `await params` first

### Allowed APIs — FastAPI + SQLAlchemy

| API | Import | Notes |
|---|---|---|
| `FastAPI` | `from fastapi import FastAPI` | App factory |
| `BaseModel` | `from pydantic import BaseModel` | Request/response schemas |
| `CORSMiddleware` | `from fastapi.middleware.cors import CORSMiddleware` | `allow_origins=["http://localhost:3000"]` |
| `create_async_engine` | `from sqlalchemy.ext.asyncio import create_async_engine` | Connection string: `postgresql+asyncpg://...` |
| `async_sessionmaker` | `from sqlalchemy.ext.asyncio import async_sessionmaker` | `expire_on_commit=False` |
| `AsyncSession` | `from sqlalchemy.ext.asyncio import AsyncSession` | Via `Depends` + `yield` generator |
| `DeclarativeBase` | `from sqlalchemy.orm import DeclarativeBase` | Base class for ORM models |
| `Mapped[T]` + `mapped_column` | `from sqlalchemy.orm import Mapped, mapped_column` | `Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)` |
| `AsyncAttrs` | `from sqlalchemy.ext.asyncio import AsyncAttrs` | Mixin for awaitable attribute access |
| Alembic async | `alembic init -t async alembic` | Generates async `env.py` scaffold |
| `Depends` | `from fastapi import Depends` | DI for session: `SessionDep = Annotated[AsyncSession, Depends(get_session)]` |

**Anti-patterns:**
- Do NOT use sync `Session` or `create_engine` — everything must be async
- Do NOT forget `expire_on_commit=False` on `async_sessionmaker`
- Do NOT use `response_model` and return raw ORM objects — convert to Pydantic first

---

## Phase 1: Project Scaffold + Infrastructure

**Goal:** Working project structure with Docker Postgres, FastAPI skeleton, and Next.js skeleton. All three can start and connect.

### Tasks

1. **Create project root structure:**
   ```
   agent-learn/
     backend/
       app/
         __init__.py
         main.py          # FastAPI app
         config.py         # settings (DB URL, OpenRouter key)
         models.py         # SQLAlchemy ORM models
         schemas.py        # Pydantic request/response models
         database.py       # engine, session factory, get_session
         routers/
           __init__.py
           courses.py      # course endpoints
       alembic/
       alembic.ini
       requirements.txt
       pyproject.toml
     frontend/
       (Next.js project via create-next-app)
     docker-compose.yml
     Makefile
   ```

2. **docker-compose.yml** with Postgres 16:
   - Service: `db`, image: `postgres:16`, port 5432
   - Environment: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
   - Named volume for data persistence

3. **FastAPI skeleton** (`backend/app/main.py`):
   - Create `FastAPI()` app
   - Add CORS middleware allowing `http://localhost:3000`
   - Include courses router
   - Health check endpoint: `GET /api/health`

4. **Database setup** (`backend/app/database.py`):
   - `create_async_engine` with `postgresql+asyncpg://` URL from config
   - `async_sessionmaker(engine, expire_on_commit=False)`
   - `get_session()` async generator dependency

5. **Config** (`backend/app/config.py`):
   - Pydantic `BaseSettings` for `DATABASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
   - Load from environment / `.env` file

6. **Alembic init:**
   - `alembic init -t async alembic` inside `backend/`
   - Configure `alembic/env.py` to use async engine from `database.py`
   - Configure `alembic.ini` with the DB URL

7. **Next.js scaffold:**
   - `npx create-next-app@latest frontend` with App Router + Tailwind + TypeScript
   - Install `react-markdown` and `@tailwindcss/typography`
   - Verify dev server starts

8. **Makefile** with targets:
   - `make dev-db` — `docker compose up -d db`
   - `make dev-backend` — `cd backend && uvicorn app.main:app --reload`
   - `make dev-frontend` — `cd frontend && npm run dev`
   - `make dev` — starts all three

9. **Backend requirements.txt:**
   - `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`
   - `deepagents`, `langchain`, `langchain-openai`
   - `pydantic-settings`, `python-dotenv`

### Documentation references
- FastAPI app creation: Phase 0 FastAPI section
- Async engine + session: Phase 0 SQLAlchemy section
- CORS middleware: Phase 0 FastAPI section (`CORSMiddleware`)
- Next.js scaffold: `npx create-next-app@latest` with Tailwind option
- Alembic async: `alembic init -t async`

### Verification checklist
- [ ] `docker compose up -d db` starts Postgres, accepting connections on 5432
- [ ] `uvicorn app.main:app --reload` starts FastAPI, `GET /api/health` returns 200
- [ ] `npm run dev` starts Next.js on port 3000
- [ ] FastAPI CORS allows requests from localhost:3000

### Anti-pattern guards
- Do NOT hardcode database credentials — use environment variables
- Do NOT use sync SQLAlchemy engine — must be `create_async_engine`
- Do NOT skip `expire_on_commit=False`

---

## Phase 2: Database Models + Migrations

**Goal:** `courses` and `sections` tables exist in Postgres with a working migration.

### Tasks

1. **ORM models** (`backend/app/models.py`):
   - `Base` class inheriting `AsyncAttrs, DeclarativeBase`
   - `Course` model: `id` (UUID PK), `topic` (text), `instructions` (text nullable), `status` (text, default "outline_ready"), `created_at`, `updated_at`
   - `Section` model: `id` (UUID PK), `course_id` (FK to courses.id), `position` (int), `title` (text), `summary` (text), `content` (text nullable), `created_at`, `updated_at`
   - Relationship: `Course.sections` → `Section` (ordered by position)

2. **Pydantic schemas** (`backend/app/schemas.py`):
   - `CourseCreate`: `topic: str`, `instructions: str | None = None`
   - `SectionOutline`: `position: int`, `title: str`, `summary: str`
   - `SectionFull`: extends SectionOutline with `content: str | None`
   - `CourseResponse`: `id: UUID`, `topic: str`, `instructions: str | None`, `status: str`, `sections: list[SectionFull]`
   - `GenerateResponse`: `id: UUID`, `status: str`, `sections: list[SectionFull]`

3. **Create Alembic migration:**
   - Import `Base` metadata in `alembic/env.py`
   - `alembic revision --autogenerate -m "create courses and sections tables"`
   - `alembic upgrade head`

4. **API endpoint stubs** (`backend/app/routers/courses.py`):
   - `POST /api/courses` — accepts `CourseCreate`, returns `CourseResponse` (stub: creates DB row with mock outline)
   - `POST /api/courses/{id}/generate` — returns `GenerateResponse` (stub: fills content with placeholder text)
   - `GET /api/courses/{id}` — returns `CourseResponse`

### Documentation references
- UUID PKs: `Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)`
- Async session in endpoints: `SessionDep = Annotated[AsyncSession, Depends(get_session)]`
- Pydantic schemas: Phase 0 FastAPI section

### Verification checklist
- [ ] `alembic upgrade head` creates both tables in Postgres
- [ ] `POST /api/courses` with `{"topic": "test"}` creates a row and returns JSON with `id` and `status: "outline_ready"`
- [ ] `GET /api/courses/{id}` returns the created course
- [ ] `POST /api/courses/{id}/generate` returns sections with placeholder content
- [ ] All endpoints return proper Pydantic-serialized JSON (no raw ORM objects)

### Anti-pattern guards
- Do NOT return raw SQLAlchemy model instances from endpoints — convert to Pydantic schemas
- Do NOT forget to `await session.commit()` after writes
- Do NOT use `session.add()` without `await session.flush()` if you need the generated UUID back

---

## Phase 3: Deep Agents — Planner Subagent

**Goal:** `POST /api/courses` calls the Deep Agents supervisor, which delegates to the planner subagent, and returns a structured outline.

### Tasks

1. **Agent module** (`backend/app/agent.py`):
   - Create `get_model()` function using `init_chat_model(model_provider="openai", base_url="https://openrouter.ai/api/v1", api_key=settings.OPENROUTER_API_KEY, model=settings.OPENROUTER_MODEL)`
   - Define planner subagent config dict:
     ```python
     planner_subagent = {
         "name": "planner",
         "description": "Generates a structured course outline from a topic and optional learner instructions",
         "system_prompt": PLANNER_SYSTEM_PROMPT,
         "tools": [],
     }
     ```
   - Define `PLANNER_SYSTEM_PROMPT` — instructs the planner to: identify key concepts, order by dependency, produce 5-10 sections, output structured JSON

2. **Structured output for planner:**
   - Define Pydantic model for planner output:
     ```python
     class OutlineSection(BaseModel):
         position: int
         title: str
         summary: str

     class CourseOutline(BaseModel):
         sections: list[OutlineSection]
     ```
   - Use `response_format=ToolStrategy(CourseOutline)` on the planner subagent (or on the supervisor — verify which level accepts this)

3. **Supervisor setup:**
   - `create_deep_agent(model=get_model(), system_prompt=SUPERVISOR_PROMPT, subagents=[planner_subagent])`
   - `SUPERVISOR_PROMPT` instructs the supervisor to delegate outline generation to the planner subagent via `task()`

4. **Wire into endpoint** (`backend/app/routers/courses.py`):
   - Replace stub `POST /api/courses` with real agent invocation
   - Input: `{"messages": [{"role": "user", "content": f"Generate a course outline for: {topic}. Instructions: {instructions}"}]}`
   - Extract `structured_response` or parse the planner's output
   - Create `Course` row + `Section` rows from outline
   - Return `CourseResponse`

### Documentation references
- `create_deep_agent()` signature: Phase 0 Deep Agents section
- `init_chat_model` with OpenRouter: `model_provider="openai"`, `base_url` param
- Subagent dict config: `name`, `description`, `system_prompt`, `tools`
- Structured output: `ToolStrategy(PydanticModel)`, result in `["structured_response"]`

### Verification checklist
- [ ] Agent initializes without error
- [ ] `POST /api/courses` with `{"topic": "Python basics"}` returns an outline with 5-10 sections
- [ ] Each section has `position`, `title`, and `summary` fields
- [ ] Sections are saved to the database
- [ ] Planner output is structured JSON (not freeform text that needs parsing)

### Anti-pattern guards
- Do NOT put the OpenRouter API key in code — load from `config.py` / environment
- Do NOT run the agent synchronously if it blocks the event loop — verify `agent.invoke()` is compatible with FastAPI's async; may need `asyncio.to_thread()` or `agent.ainvoke()`
- Do NOT skip structured output — freeform text parsing is fragile
- Verify the actual pip package name for Deep Agents at install time

---

## Phase 4: Deep Agents — Writer Subagent

**Goal:** `POST /api/courses/{id}/generate` calls the supervisor, which delegates to the writer subagent, generating lesson content for each section sequentially.

### Tasks

1. **Writer subagent config** (add to `backend/app/agent.py`):
   - Define `WRITER_SYSTEM_PROMPT` — instructs the writer to:
     - Generate markdown lesson content for each section
     - Follow the lesson structure: title, why this matters, main explanation, examples, key takeaways, what comes next
     - Receive the full outline for coherence
     - Generate sections in order, referencing earlier sections for continuity
   - Writer subagent dict:
     ```python
     writer_subagent = {
         "name": "writer",
         "description": "Generates markdown lesson content for each section of an approved course outline",
         "system_prompt": WRITER_SYSTEM_PROMPT,
         "tools": [],
     }
     ```

2. **Structured output for writer:**
   - Define Pydantic model:
     ```python
     class SectionContent(BaseModel):
         position: int
         content: str  # markdown

     class CourseContent(BaseModel):
         sections: list[SectionContent]
     ```
   - Use `response_format=ToolStrategy(CourseContent)`

3. **Update supervisor** to include both subagents:
   - `create_deep_agent(model=get_model(), system_prompt=SUPERVISOR_PROMPT, subagents=[planner_subagent, writer_subagent])`
   - Update `SUPERVISOR_PROMPT` to describe when to use each subagent

4. **Wire into endpoint** (`backend/app/routers/courses.py`):
   - Replace stub `POST /api/courses/{id}/generate`
   - Load course + sections from DB
   - Build message with full outline context for the writer
   - Invoke supervisor → writer subagent
   - Update each `Section.content` with generated markdown
   - Set `Course.status = "completed"`
   - Return `GenerateResponse`

5. **Error handling:**
   - If agent invocation fails, set `Course.status = "failed"`
   - If writer returns fewer sections than expected, set status to "failed"

### Documentation references
- Same Deep Agents APIs as Phase 3
- Supervisor with multiple subagents: Phase 0 Deep Agents section (multi-agent example)

### Verification checklist
- [ ] `POST /api/courses/{id}/generate` for an existing course returns sections with markdown content
- [ ] Each section follows the lesson structure (title, why this matters, explanation, examples, takeaways, what's next)
- [ ] `Course.status` transitions: `outline_ready` → `generating` → `completed`
- [ ] On failure, `Course.status` is set to `failed`
- [ ] `GET /api/courses/{id}` after generation returns full course with content

### Anti-pattern guards
- Do NOT generate sections independently without outline context — the writer must see the full outline
- Do NOT skip the status transition to `generating` before invoking the agent
- Do NOT silently swallow agent errors — set status to `failed` and return a useful error

---

## Phase 5: Frontend Pages

**Goal:** Three working pages that call the FastAPI backend and display course content.

### Tasks

1. **API client** (`frontend/src/lib/api.ts`):
   - `createCourse(topic: string, instructions?: string)` → POST /api/courses
   - `generateCourse(id: string)` → POST /api/courses/{id}/generate
   - `getCourse(id: string)` → GET /api/courses/{id}
   - Base URL from `NEXT_PUBLIC_API_URL` env var (default `http://localhost:8000`)

2. **Topic Input page** (`frontend/src/app/page.tsx`):
   - Client component (`'use client'`) for form state
   - Topic input field + instructions textarea
   - "Generate Course" button
   - On submit: call `createCourse()`, show loading spinner, redirect to `/courses/{id}` on success
   - Error state: show message, allow retry

3. **Outline Review page** (`frontend/src/app/courses/[id]/page.tsx`):
   - Server component that fetches course via `getCourse(id)`
   - Display: course topic, section count, list of sections with titles and summaries
   - "Approve & Generate" button (client component): calls `generateCourse(id)`, shows loading state, redirects to `/courses/{id}/learn` on success
   - "Regenerate" button: navigates to `/` with topic as query param
   - `loading.tsx` for initial page load

4. **Lesson Reader page** (`frontend/src/app/courses/[id]/learn/page.tsx`):
   - Server component that fetches full course
   - Sidebar: list of section titles, current section highlighted
   - Main content area: render section markdown with `react-markdown` wrapped in `<div className="prose">`
   - Prev/next navigation at bottom
   - Section selection via query param or client-side state
   - `loading.tsx` for initial load

5. **Shared layout** (`frontend/src/app/layout.tsx`):
   - Import `globals.css` with Tailwind
   - Minimal header with "agent-learn" title linking to `/`

6. **TypeScript types** (`frontend/src/lib/types.ts`):
   - `Course`, `Section` types matching API response shapes

### Documentation references
- Dynamic routes: `app/courses/[id]/page.tsx` with `params: Promise<{ id: string }>`
- Must `await params` before accessing `.id`
- `useRouter` from `next/navigation` for programmatic navigation
- `react-markdown` for rendering, `prose` class from `@tailwindcss/typography`
- `loading.tsx` file convention for Suspense fallbacks

### Verification checklist
- [ ] Home page renders, form submits, redirects to outline page
- [ ] Outline page displays sections with titles and summaries
- [ ] "Approve & Generate" triggers generation and redirects to reader
- [ ] "Regenerate" navigates back to home with topic prefilled
- [ ] Lesson reader renders markdown content with proper formatting
- [ ] Sidebar navigation switches between sections
- [ ] Prev/next buttons work
- [ ] Loading states show during API calls

### Anti-pattern guards
- Do NOT use `params.id` synchronously — must `await params`
- Do NOT import `useRouter` from `next/router`
- Do NOT make API calls in server components to localhost during build (use runtime fetch only)
- Do NOT forget `'use client'` directive on components with hooks or event handlers

---

## Phase 6: Integration + Verification

**Goal:** End-to-end flow works. All pieces connected. Clean local dev experience.

### Tasks

1. **End-to-end manual test:**
   - Start all services (`make dev`)
   - Enter a topic on the home page
   - Review the generated outline
   - Approve and wait for generation
   - Read through sections in the lesson reader
   - Verify shareable URL works (open `/courses/{id}` in a new tab)

2. **Backend tests** (`backend/tests/`):
   - `test_health.py` — health endpoint returns 200
   - `test_courses.py` — test course creation, generation, and retrieval with mocked agent responses
   - One integration test that calls OpenRouter for real (marked with `pytest.mark.integration`)
   - Use `httpx.AsyncClient` with FastAPI's `TestClient`

3. **Frontend smoke tests:**
   - Pages render without errors
   - Navigation between pages works

4. **Error handling pass:**
   - Verify 404 for non-existent course ID
   - Verify error display when API is down
   - Verify error display when generation fails

5. **Clean up:**
   - `.env.example` with required environment variables
   - `.gitignore` for `node_modules`, `__pycache__`, `.env`, `.superpowers/`
   - Verify `make dev` starts everything cleanly from a fresh clone

### Verification checklist
- [ ] Full end-to-end flow works: topic → outline → approve → read lessons
- [ ] Backend tests pass: `pytest backend/tests/`
- [ ] Frontend builds without errors: `npm run build`
- [ ] Shareable URL works (GET /api/courses/{id} + page reload)
- [ ] Error states display correctly
- [ ] `.env.example` documents all required variables
- [ ] `make dev` starts everything from scratch

### Anti-pattern guards
- Do NOT skip the real integration test — at least one test must hit OpenRouter to verify the pipeline works
- Do NOT commit `.env` files
- Do NOT leave placeholder/stub code from Phase 2 in the final codebase

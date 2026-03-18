# Milestone 1 — Topic to Draft Course

**Status:** Design approved
**Date:** 2026-03-18
**Scope:** Vertical slice proving the approval flow and draft lesson pipeline end-to-end

## Summary

A learner enters a topic and optional instructions. The system generates a course outline. The learner reviews the outline and either approves it or regenerates with different instructions. On approval, the system generates draft lessons for all sections sequentially. The learner reads the course in a section-by-section reader.

No auth, no background jobs, no streaming. Synchronous generation. Anonymous courses with shareable URLs.

## Architecture

Two services, one database, synchronous flow.

- **Next.js (App Router)** — frontend with three routes
- **FastAPI** — backend with three endpoints, hosts the Deep Agents supervisor
- **Docker Postgres** — local database, SQLAlchemy (async) ORM, Alembic migrations
- **OpenRouter** — LLM provider via Deep Agents' `init_chat_model`

### Flow

```
Topic + instructions
  → POST /api/courses
  → Planner subagent generates outline
  → Saved to Postgres
  → Returned to frontend for review
  → User approves
  → POST /api/courses/{id}/generate
  → Writer subagent generates all sections sequentially
  → Each section saved to Postgres as it completes
  → Frontend shows lesson reader
```

## API

### POST /api/courses

Generate a course outline from a topic.

**Request:**
```json
{ "topic": "string", "instructions": "string | null" }
```

**Response:**
```json
{
  "id": "uuid",
  "topic": "string",
  "instructions": "string | null",
  "status": "outline_ready",
  "outline": [
    { "position": 1, "title": "string", "summary": "string" }
  ]
}
```

### POST /api/courses/{id}/generate

Generate draft lessons for an approved outline.

**Request:** No body. Course ID in path.

**Response:**
```json
{
  "id": "uuid",
  "status": "completed",
  "sections": [
    { "position": 1, "title": "string", "content": "string (markdown)" }
  ]
}
```

### GET /api/courses/{id}

Retrieve a course with its outline and sections (if generated). Used for shareable URLs and page reloads.

**Response:**
```json
{
  "id": "uuid",
  "topic": "string",
  "instructions": "string | null",
  "status": "outline_ready | generating | completed | failed",
  "sections": [
    {
      "position": 1,
      "title": "string",
      "summary": "string",
      "content": "string (markdown) | null"
    }
  ]
}
```

### Regeneration Behavior

"Regenerate" on the outline review page navigates back to the input page with the topic prefilled. Submitting again creates a new `POST /api/courses` call and a new course row. Old unused courses are left in the DB — no cleanup in M1.

## Data Models

### courses

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK, default gen_random_uuid() |
| topic | text | not null |
| instructions | text | nullable |
| status | text | outline_ready \| generating \| completed \| failed |
| created_at | timestamptz | default now() |
| updated_at | timestamptz | default now() |

### sections

| Column | Type | Notes |
|---|---|---|
| id | uuid | PK, default gen_random_uuid() |
| course_id | uuid | FK → courses.id, not null |
| position | integer | ordering within course |
| title | text | not null |
| summary | text | outline description, set by planner |
| content | text | nullable, markdown lesson body, set by writer |
| created_at | timestamptz | default now() |
| updated_at | timestamptz | default now() |

Sections serve double duty: the planner creates rows with title + summary + position (content null). The writer fills in content.

## Agent Behavior

### Supervisor

- Created via `create_deep_agent()` with OpenRouter model
- Receives API requests, delegates to the right subagent, persists results to Postgres
- Does not generate course content — pure coordination

### Planner Subagent

- **Input:** topic + instructions
- **Output:** structured JSON array of `{ position, title, summary }`
- System prompt instructs it to: identify key concepts, order by dependency, write a concise summary per section
- Targets 5-10 sections (adapts to topic scope)
- No web search, no retrieval — model knowledge only in M1

### Writer Subagent

- **Input:** full outline (all titles + summaries) plus topic and instructions
- **Output:** markdown lesson content for each section, generated sequentially
- Receives the full outline for coherence — each section written knowing what comes before and after
- Each section follows the lesson structure: title, why this matters, main explanation, examples, key takeaways, what comes next
- No citations in M1 (that's M2)

Both subagents use the same OpenRouter model. The supervisor doesn't make LLM calls in M1 — it's routing only.

## Frontend Pages

### / — Topic Input

- Topic text field
- Optional instructions textarea
- "Generate Course" button
- Loading spinner while planner works

### /courses/[id] — Outline Review

- Displays generated outline (numbered sections with titles and summaries)
- "Approve & Generate" button — triggers lesson generation, shows loading state
- "Regenerate" button — returns to input page with topic prefilled
- No inline editing — approve or regenerate

### /courses/[id]/learn — Lesson Reader

- Sidebar listing all sections (current section highlighted)
- Main content area rendering markdown
- Prev/next navigation at bottom
- Styled with Tailwind, no component library

## Error Handling

- LLM call fails → 500 with message, frontend shows "Something went wrong, try again"
- Malformed LLM output → retry once, then fail with 500
- Course not found → 404
- No global error recovery or retry queues (that's M3)

## Testing

- **Backend:** pytest with async test client. Mocked LLM responses for fast tests. One integration test hitting OpenRouter for real.
- **Frontend:** smoke tests — pages render, navigation works. No E2E browser tests.
- **Agent:** test planner and writer subagent configs in isolation — verify output structure.

## Local Dev Setup

- `docker-compose.yml` with Postgres
- FastAPI server with hot reload
- Next.js dev server
- Root-level script or Makefile to start everything

## Decisions Log

| Decision | Choice | Rationale |
|---|---|---|
| Backend framework | FastAPI (separate from Next.js) | Agent layer is Python, will grow through M2-M5 |
| Agent harness | Deep Agents | Built-in planning, subagent isolation, filesystem backend, LangGraph runtime |
| Database | Docker Postgres (local) | No external dependency, migrate to hosted later |
| LLM provider | OpenRouter | Model flexibility without LiteLLM dependency |
| ORM | SQLAlchemy (async) + Alembic | Standard Python DB stack |
| Frontend | Next.js App Router + Tailwind | Committed stack choice |
| Outline editing | Approve/reject only | Keeps M1 focused, inline editing can come later |
| Writer strategy | Sequential, single subagent | Coherence over speed in M1, restructure for parallel in M3 |
| Auth | None | Anonymous courses with shareable IDs, auth in M3 |

## Out of Scope (M1)

- Research / web search
- Evidence cards / verification
- Editorial smoothing
- Diagrams / quizzes
- Background jobs / SSE / progressive delivery
- Auth / user accounts
- Inline outline editing
- Spaced repetition / adaptive learning

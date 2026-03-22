# Live Generation Feed — Discovery + Pipeline

**Date:** 2026-03-22
**Status:** Draft
**Scope:** Real-time SSE feeds for both course creation (discovery + planning) and full pipeline generation (research → verify → write → edit)

## Problem

Two long waits with no feedback:

1. **Course creation (15-45s):** `POST /courses` blocks while running discovery research and planning. User sees a spinner.
2. **Pipeline generation (4-10 min):** After approving the outline, the pipeline runs. The current SSE shows only stage-level transitions ("researching" → "writing" → "editing") with no detail about what's happening inside each stage.

## Solution

Stream both processes to the frontend with full transparency. Dedicated feed pages (`/courses/{id}/discover` and `/courses/{id}/generating`) show search queries, sources, synthesis, section progress, and content being written in real-time.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Discovery SSE source | `POST /courses` streams events, replay via `GET /discover/stream` | Single endpoint for creation; GET for page reconnection |
| Pipeline SSE source | Enhance existing `GET /pipeline/stream` with granular events | Already built; extend, don't replace |
| Feed detail level | Full transparency for both phases | Consistent experience, user sees value being created |
| Discovery completion | Auto-redirect to outline review after 2.5s | Seamless flow |
| Pipeline completion | Auto-redirect to learn page after 2.5s | Matches existing behavior |
| Revisit behavior | Static summary from DB when buffer is gone | Feed events are ephemeral |
| Event replay | In-memory buffer (5 min TTL) for reconnection | Short-lived, not worth DB persistence |
| Pipeline section display | Sections in course order, events appear as they happen | Clean layout, naturally shows parallel work |

## Design

### 1. Shared Event Infrastructure

Both phases use the same callback mechanism:

```python
EventCallback = Callable[[str, dict], Awaitable[None]]
```

An `asyncio.Queue` per active operation, with events buffered in-memory for replay. A thin `emit(event, data)` wrapper pushes to the queue AND appends to the buffer. Functions accept an optional `on_event` callback — when absent, they behave identically to today.

**Buffer storage (module-level in courses.py):**
```python
_feed_events: dict[str, list[dict]] = {}     # course_id → buffered events
_feed_queues: dict[str, asyncio.Queue] = {}   # course_id → live queue (while in progress)
```

**Cleanup:** `asyncio.get_event_loop().call_later(300, cleanup_fn)` scheduled when a terminal event (`complete`, `error`) is emitted. Both the buffer and queue are removed. Error events also get the 5-minute TTL (not immediate deletion) so clients can reconnect and see the error.

**Keepalive:** During long LLM calls (synthesis, planning, writing), emit `:keepalive\n\n` SSE comments every 15 seconds to prevent proxy/browser timeouts.

### 2. Discovery Event Protocol

The `POST /courses` endpoint changes from returning `CourseResponse` JSON to an SSE stream.

**Session management:** The handler commits the initial course row before starting the background task. The `run_discovery()` task opens its own session via `async with async_session() as session:` — it does NOT use the request-scoped `SessionDep`. This avoids the session lifetime conflict where the dependency is disposed before the stream finishes.

**Queue registration:** The queue is registered in `_feed_queues[course_id]` at creation time so the replay endpoint can follow live events.

**Event sequence:**

| Event | Data | When |
|---|---|---|
| `created` | `{course_id}` | Course row committed |
| `query` | `{index, total, text}` | Each search query dispatched |
| `source` | `{query_index, title, url, snippet}` | Individual source found |
| `query_done` | `{index, total, result_count}` | One query completed |
| `discovery_done` | `{total_sources}` | All searches finished |
| `synthesizing` | `{}` | Discovery researcher agent invoked |
| `synthesis_done` | `{key_concepts, subtopics}` | Discovery researcher finished |
| `planning` | `{}` | Planner agent invoked |
| `section` | `{position, title, summary}` | Each section as planned |
| `complete` | `{course_id, status, section_count, ungrounded}` | Outline ready |
| `ungrounded` | `{message}` | Discovery failed, proceeding without research |
| `error` | `{message}` | Fatal error |

### 3. Pipeline Event Protocol

The existing `GET /courses/{id}/pipeline/stream` is enhanced with granular events emitted from the worker via the same callback mechanism.

**How events reach the SSE endpoint:** The worker calls `run_pipeline()` which emits events via callback. These events are written to the `_feed_events` buffer and `_feed_queues` queue. The SSE endpoint reads from the queue.

**Cross-process challenge:** The worker runs as a separate process, but the SSE endpoint runs on the API server. Events can't share in-memory queues across processes.

**Solution:** The worker writes pipeline events to a new `pipeline_events` column (JSONB array, append-only) on the `pipeline_jobs` row. The SSE endpoint polls this column every 2 seconds (same as current) and emits new events since the last poll. This is a simple extension of the existing poll-and-push pattern — no new infrastructure.

```sql
-- Add to pipeline_jobs table
ALTER TABLE pipeline_jobs ADD COLUMN events JSONB DEFAULT '[]'::jsonb;
```

**Pipeline event sequence (per section):**

| Event | Data | When |
|---|---|---|
| `pipeline_start` | `{total_sections}` | Pipeline begins |
| `research_start` | `{section, title}` | Section research begins |
| `research_source` | `{section, title, url, snippet}` | Source found during research |
| `research_done` | `{section, card_count}` | Section research complete |
| `verify_start` | `{section, title}` | Verification begins |
| `verify_done` | `{section, verified_count, rejected_count}` | Verification complete |
| `write_start` | `{section, title}` | Writing begins |
| `write_done` | `{section}` | Section content written |
| `edit_start` | `{section, title}` | Editing begins |
| `edit_done` | `{section, glossary_terms, concepts}` | Edit complete, blackboard updated |
| `pipeline_complete` | `{status, completed_sections, failed_sections}` | Pipeline finished |
| `pipeline_error` | `{message, section}` | Section or pipeline error |

### 4. Backend — `POST /courses` Changes

```python
@router.post("/courses")
@limiter.limit("5/minute")
async def create_course(request, body, session, user_id):
    # Create and COMMIT course row (so background task can read it)
    course = Course(topic=body.topic, instructions=body.instructions, status="researching", user_id=user_id)
    session.add(course)
    await session.commit()  # committed, not just flushed

    course_id_str = str(course.id)
    queue = asyncio.Queue()  # unbounded, no maxsize
    _feed_events[course_id_str] = []
    _feed_queues[course_id_str] = queue

    async def emit(event, data):
        entry = {"event": event, "data": data}
        await queue.put(entry)
        _feed_events[course_id_str].append(entry)

    async def run_discovery():
        await emit("created", {"course_id": course_id_str})
        try:
            # Own session — request session is gone by now
            async with async_session() as sess:
                provider, model, creds, extra_fields = await _get_user_provider(user_id, sess)
                search_provider, search_creds = await _get_user_search_provider(user_id, sess)

            outline, ungrounded = await generate_outline(
                body.topic, body.instructions, provider, model, creds, extra_fields,
                search_provider, search_creds, on_event=emit,
            )

            # Save sections and briefs (own session)
            async with async_session() as sess:
                course_row = await sess.get(Course, course.id)
                course_row.ungrounded = ungrounded
                for sec in outline.sections:
                    sess.add(Section(course_id=course.id, position=sec.position, title=sec.title, summary=sec.summary))
                    await emit("section", {"position": sec.position, "title": sec.title, "summary": sec.summary})
                for brief in outline.research_briefs:
                    sess.add(ResearchBrief(course_id=course.id, section_position=brief.section_position, questions=brief.questions, source_policy=brief.source_policy))
                course_row.status = "outline_ready"
                await sess.commit()

            await emit("complete", {"course_id": course_id_str, "status": "outline_ready", "section_count": len(outline.sections), "ungrounded": ungrounded})
        except Exception as e:
            logger.error("Discovery failed: %s", e)
            async with async_session() as sess:
                course_row = await sess.get(Course, course.id)
                if course_row:
                    course_row.status = "failed"
                    await sess.commit()
            await emit("error", {"message": str(e)[:500]})
        finally:
            await queue.put(None)  # sentinel
            _feed_queues.pop(course_id_str, None)
            # Schedule buffer cleanup in 5 minutes
            asyncio.get_event_loop().call_later(300, lambda: _feed_events.pop(course_id_str, None))

    asyncio.create_task(run_discovery())

    async def event_generator():
        while True:
            entry = await queue.get()
            if entry is None:
                break
            yield f"event: {entry['event']}\ndata: {json.dumps(entry['data'])}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

### 5. Backend — Discovery Replay Endpoint

**New endpoint:** `GET /courses/{id}/discover/stream?token=JWT`

```python
@router.get("/courses/{course_id}/discover/stream")
async def discover_stream(course_id: uuid.UUID, token: str = Query(...)):
    user_id = await get_user_from_query_token(token)

    # Ownership check
    async with async_session() as session:
        course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if not course or course.user_id != user_id:
            raise HTTPException(403, "Not authorized")

    course_id_str = str(course_id)

    async def event_generator():
        # Replay buffered events
        buffered = _feed_events.get(course_id_str, [])
        for entry in buffered:
            yield f"event: {entry['event']}\ndata: {json.dumps(entry['data'])}\n\n"

        # If still in progress, follow live queue
        queue = _feed_queues.get(course_id_str)
        if queue:
            subscriber = asyncio.Queue()
            # ... subscribe to live events ...

        # If buffer is gone, build synthetic response from DB
        if not buffered:
            async with async_session() as session:
                course = (await session.execute(
                    select(Course).options(selectinload(Course.sections)).where(Course.id == course_id)
                )).scalar_one_or_none()
                if course and course.sections:
                    for s in sorted(course.sections, key=lambda s: s.position):
                        yield f"event: section\ndata: {json.dumps({'position': s.position, 'title': s.title, 'summary': s.summary})}\n\n"
                    yield f"event: complete\ndata: {json.dumps({'course_id': course_id_str, 'status': course.status, 'section_count': len(course.sections)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 6. Backend — Enhanced Pipeline SSE

**Modified:** The worker's `run_pipeline()` appends events to `pipeline_jobs.events` (JSONB array) as a lightweight event log.

```python
async def append_pipeline_event(job_id, event, data, session):
    """Append an event to the pipeline_jobs.events JSONB array."""
    await session.execute(
        text("UPDATE pipeline_jobs SET events = events || :event::jsonb WHERE id = :job_id"),
        {"event": json.dumps([{"event": event, "data": data}]), "job_id": str(job_id)},
    )
    await session.commit()
```

**Modified:** The existing `GET /pipeline/stream` endpoint now reads from `pipeline_jobs.events` and emits new events since the last poll, in addition to the existing stage-level status events.

**Where events are emitted in the pipeline:**
- `research_section()`: emit `research_start`, `research_source` (per search result), `research_done`
- `verify_evidence()`: emit `verify_start`, `verify_done` (with counts)
- `write_section()`: emit `write_start`, `write_done`
- `edit_section()`: emit `edit_start`, `edit_done` (with blackboard update summary)

All via the same optional `on_event` callback pattern.

### 7. Database Change

Add `events` column to `pipeline_jobs`:

```sql
ALTER TABLE pipeline_jobs ADD COLUMN events JSONB NOT NULL DEFAULT '[]'::jsonb;
```

Small Alembic migration. The column stores an array of `{event, data}` objects appended during pipeline execution.

### 8. Frontend — Discovery Page

**New file:** `frontend/src/app/courses/[id]/discover/page.tsx`

Connects to `GET /courses/{id}/discover/stream?token=JWT` via EventSource.

**Live mode:** Events render in a terminal-like feed with dark background card, monospace for queries, source titles as links, sections fading in. Pulsing dot on the active step. Auto-scroll.

**Static mode (buffer gone):** Fetch course, render sections as completed feed. "Review Outline" button.

**On `complete`:** Show "Outline ready! Redirecting..." → navigate to `/courses/{id}` after 2.5 seconds.

### 9. Frontend — Generating Page

**New file:** `frontend/src/app/courses/[id]/generating/page.tsx`

Connects to the enhanced `GET /courses/{id}/pipeline/stream?token=JWT`.

**Layout:** Sections displayed in course order. Each section has a collapsible block showing:
- Research: sources found with titles/URLs
- Verification: card counts (verified/rejected)
- Writing: "Writing..." indicator
- Editing: glossary terms and concepts added

Events appear as they happen — section 3 might show "writing" while section 1 shows "editing" and section 5 shows "researching". The order is by section position, with each section's latest state highlighted.

**On `pipeline_complete`:** Show "Course ready! Redirecting..." → navigate to `/courses/{id}/learn` after 2.5 seconds.

**Static mode:** If revisiting after completion, fetch course sections and show as completed feed.

### 10. Frontend — Navigation Changes

**Home page (`page.tsx`):**
1. Call `createCourseStream()` — reads first `created` event for course_id
2. Navigate to `/courses/{courseId}/discover`

**Outline review page (`courses/[id]/page.tsx`):**
1. On "Generate" click, call `generateCourse()` (returns `{job_id}`)
2. Navigate to `/courses/{courseId}/generating` instead of showing PipelineProgress inline

**PipelineProgress component:** Kept as-is for backward compatibility. The generating page uses the enhanced SSE directly.

### 11. Frontend — API Client Changes

**Keep:** `createCourse()` for backward compatibility (regenerate flow).

**Add:**
- `createCourseStream(topic, instructions, token)` → POST with SSE, reads first event, returns `{courseId: string}`
- `getDiscoverStreamUrl(courseId, token)` → GET SSE URL
- `getPipelineStreamUrl(courseId, token)` → already exists, enhanced events

### 12. Changes to `agent_service.py`

Add optional `on_event: EventCallback | None = None` to:
- `discover_topic()` — emits query, source, query_done, discovery_done, synthesizing, synthesis_done
- `generate_outline()` — passes through to discover_topic, emits planning, ungrounded
- `research_section()` — emits research_source per result
- `verify_evidence()` — emits verify counts
- `write_section()` — emits write_start/write_done
- `edit_section()` — emits edit_done with blackboard summary

All callbacks are optional. When `None`, zero behavior change — existing tests and the `/regenerate` endpoint work unchanged.

## Files Changed

| File | Change |
|---|---|
| `backend/app/routers/courses.py` | SSE `POST /courses`, `GET /discover/stream`, enhance `GET /pipeline/stream`, in-memory buffer/queue |
| `backend/app/agent_service.py` | Add `on_event` callbacks to discover, outline, research, verify, write, edit functions |
| `backend/app/pipeline.py` | Thread `on_event` into pipeline phases, add `append_pipeline_event` helper |
| `backend/app/models.py` | Add `events` JSONB column to PipelineJob |
| `backend/alembic/versions/xxx_add_pipeline_events.py` | **New.** Migration for events column |
| `frontend/src/app/page.tsx` | Use `createCourseStream()`, redirect to discover page |
| `frontend/src/app/courses/[id]/discover/page.tsx` | **New.** Discovery feed page |
| `frontend/src/app/courses/[id]/generating/page.tsx` | **New.** Pipeline generation feed page |
| `frontend/src/app/courses/[id]/page.tsx` | Redirect to generating page on "Generate" click |
| `frontend/src/lib/api.ts` | Add `createCourseStream()`, `getDiscoverStreamUrl()` |
| `backend/tests/test_courses.py` | Update tests for SSE response from `POST /courses` |

## Out of Scope

- Persisting discovery events to the database (only pipeline events are persisted)
- Replay of search snippets after buffer expires
- Discovery feed for the `/regenerate` endpoint
- WebSocket transport
- Streaming section content as it's being written (typewriter effect) — could be added later

# Milestone 4 — Implementation Plan

**Design spec:** `docs/superpowers/specs/2026-03-19-milestone-4-design.md`
**Date:** 2026-03-19

---

## Phase 0: Documentation Discovery (Allowed APIs)

### Backend Patterns (from codebase exploration)

| Pattern | Location | API |
|---------|----------|-----|
| Router registration | `backend/app/main.py:15-16` | `app.include_router(router, prefix="/api")` |
| Auth dependency | `backend/app/auth.py:32-57` | `user_id: str = Depends(get_current_user)` returns Clerk `sub` claim |
| DB session | `backend/app/database.py:11-15` | `session: SessionDep` (Annotated AsyncSession) |
| Model base | `backend/app/models.py:9-10` | `class Base(AsyncAttrs, DeclarativeBase)` |
| UUID PK | `backend/app/models.py:16` | `mapped_column(primary_key=True, default=uuid.uuid4)` |
| FK pattern | `backend/app/models.py:44-46` | `mapped_column(ForeignKey("courses.id"), nullable=False)` |
| Unique constraint | `backend/app/models.py:134` | `__table_args__ = (UniqueConstraint(...),)` |
| Settings | `backend/app/config.py:4-17` | `class Settings(BaseSettings)` with `model_config = {"env_file": ".env"}` |
| Pydantic schemas | `backend/app/schemas.py` | `BaseModel` with `model_config = {"from_attributes": True}` for responses |
| Migration cmd | Makefile | `cd backend && uv run alembic revision --autogenerate -m "desc"` |
| httpx usage | `backend/app/routers/courses.py:145-155` | `async with httpx.AsyncClient() as client: resp = await client.post(...)` |

### FastAPI SSE Streaming

| API | Import | Usage |
|-----|--------|-------|
| `StreamingResponse` | `from fastapi.responses import StreamingResponse` | `StreamingResponse(async_generator(), media_type="text/event-stream")` |
| Headers | N/A | `Cache-Control: no-cache`, `X-Accel-Buffering: no` |

### httpx Async Streaming (proxy upstream SSE)

| API | Usage |
|-----|-------|
| `client.stream("POST", url, json=payload, headers=headers)` | Async context manager, yields response for iteration |
| `response.aiter_bytes()` | Async iterator yielding raw byte chunks (preserves SSE framing) |

**Copy-ready proxy pattern:**
```python
async def event_generator():
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST", "https://openrouter.ai/api/v1/chat/completions",
            json=payload, headers=headers, timeout=httpx.Timeout(120.0, connect=10.0),
        ) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

return StreamingResponse(event_generator(), media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

### OpenRouter API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/models` | GET | List models. Returns `{ data: [{ id, name, context_length, pricing, architecture }] }` |
| `/api/v1/chat/completions` | POST | Chat with `stream: true`. SSE: `data: {"choices":[{"delta":{"content":"..."}}]}`, ends with `data: [DONE]` |

**Auth:** `Authorization: Bearer {OPENROUTER_API_KEY}`
**Filter for text chat models:** `architecture.input_modalities` contains `"text"` AND `architecture.output_modalities` contains `"text"`

### Mermaid.js in React

| API | Usage |
|-----|-------|
| `mermaid.initialize({ startOnLoad: false, theme: 'dark' })` | Call once at module level |
| `mermaid.render(id, definition)` | Returns `Promise<{ svg: string }>`. Does NOT touch DOM. |
| react-markdown `components.code` | Receives `{ className, children }`. Fenced blocks get `className="language-mermaid"` |

**React component pattern:**
- Use `mermaid.render(uniqueId, definition)` which returns `{ svg }` string
- Set SVG via ref: `ref.current.innerHTML = svg` (mermaid output is trusted local library output, not user-supplied HTML)
- Note: Consider using DOMPurify to sanitize the SVG if mermaid definitions could come from untrusted sources
- Module-level `mermaid.initialize({ startOnLoad: false, theme: 'dark' })`
- `useEffect` + `useRef` pattern with cleanup flag
- Counter-based unique IDs to avoid collisions with multiple diagrams

**react-markdown integration:**
```tsx
components={{
  code({ className, children }) {
    if (/language-mermaid/.test(className || ''))
      return <MermaidBlock definition={String(children).replace(/\n$/, '')} />;
    return <code className={className}>{children}</code>;
  },
}}
```

### Frontend Patterns (from codebase exploration)

| Pattern | Location | Detail |
|---------|----------|--------|
| API base | `frontend/src/lib/api.ts:3` | `const API_BASE = process.env.NEXT_PUBLIC_API_URL \|\| 'http://localhost:8000'` |
| Auth headers | `frontend/src/lib/api.ts:5-11` | `authHeaders(token?)` returns `{ Content-Type, Authorization? }` |
| Token retrieval | Components | `const { getToken } = useAuth()` then `await getToken()` |
| Types file | `frontend/src/lib/types.ts` | All interfaces exported from single file |
| Styling | All components | Tailwind utilities only, `prose prose-invert prose-purple` for markdown |
| Client directive | All components | `'use client'` at top |
| Markdown rendering | Learn page line 147 | `<ReactMarkdown>{content}</ReactMarkdown>` inside `prose prose-invert prose-purple max-w-none` |
| Custom components | CitationRenderer:38-65 | `components={{ code, p, li }}` pattern on ReactMarkdown |

### Anti-Patterns to Avoid

- Do NOT use `mermaid.run()` (DOM scanning) — use `mermaid.render()` (programmatic)
- Do NOT raise `HTTPException` inside a streaming generator after first yield — headers already sent
- Do NOT create a provider abstraction — OpenRouter only
- Do NOT use `response.aiter_text()` for SSE proxy — use `aiter_bytes()` to preserve framing
- Do NOT use CSS modules — Tailwind only
- Do NOT create separate type files — add to existing `types.ts`

---

## Phase 1: Data Model & Migration

**Goal:** Add `ChatMessage` model and database table.

### Tasks

1. **Add `ChatMessage` model to `backend/app/models.py`**

   Copy pattern from `LearnerProgress` model (line 120-137). New model:

   ```python
   class ChatMessage(Base):
       __tablename__ = "chat_messages"

       id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
       course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), nullable=False)
       user_id: Mapped[str] = mapped_column(Text, nullable=False)
       role: Mapped[str] = mapped_column(Text, nullable=False)  # "user" or "assistant"
       content: Mapped[str] = mapped_column(Text, nullable=False)
       model: Mapped[str | None] = mapped_column(Text, nullable=True)  # null for user messages
       section_context: Mapped[int] = mapped_column(Integer, nullable=False)
       created_at: Mapped[datetime] = mapped_column(server_default=func.now())

       course: Mapped["Course"] = relationship()
   ```

2. **Add relationship to `Course` model (optional, for eager loading)**

   Add to Course class: `chat_messages: Mapped[list["ChatMessage"]] = relationship()`

3. **Generate Alembic migration**

   ```bash
   cd backend && uv run alembic revision --autogenerate -m "create chat_messages table"
   ```

4. **Add index for conversation retrieval**

   In the migration's `upgrade()`, after the table creation add:
   ```python
   op.create_index("ix_chat_messages_conversation", "chat_messages", ["course_id", "user_id", "created_at"])
   ```

5. **Run migration**

   ```bash
   cd backend && uv run alembic upgrade head
   ```

### Verification

- [ ] `cd backend && uv run alembic heads` shows single head
- [ ] `cd backend && uv run python -c "from app.models import ChatMessage; print(ChatMessage.__tablename__)"` prints `chat_messages`
- [ ] Grep: no `TBD` or `TODO` in models.py

---

## Phase 2: Backend Chat Service & Endpoints

**Goal:** Implement `/api/chat/models`, `POST /api/courses/{id}/chat` (streaming), `GET /api/courses/{id}/chat` (history).

### Tasks

1. **Add config vars to `backend/app/config.py`**

   Add to `Settings` class:
   ```python
   CHAT_DEFAULT_MODEL: str = "anthropic/claude-sonnet-4"
   ```

2. **Add Pydantic schemas to `backend/app/schemas.py`**

   ```python
   class ChatRequest(BaseModel):
       message: str
       model: str
       section_context: int

   class ChatMessageResponse(BaseModel):
       model_config = {"from_attributes": True}
       id: UUID
       role: str
       content: str
       model: str | None
       section_context: int
       created_at: datetime

   class ChatModelInfo(BaseModel):
       id: str
       name: str
       context_length: int
       pricing_prompt: str
       pricing_completion: str
   ```

3. **Create `backend/app/chat_service.py`**

   Three functions:

   **a) `get_models()` — fetch and cache OpenRouter model list**
   - `GET https://openrouter.ai/api/v1/models` with Bearer token
   - Filter: `architecture.input_modalities` contains `"text"` AND `architecture.output_modalities` contains `"text"`
   - Return slimmed list: `{ id, name, context_length, pricing_prompt, pricing_completion }`
   - Cache result in module-level variable with 5-minute TTL
   - Use `httpx.AsyncClient` (same pattern as `courses.py:145`)

   **b) `assemble_context()` — build system prompt + message list**
   - Takes: `course_id, section_context, session` (DB session)
   - Loads: Course (with sections via `selectinload`), Blackboard, EvidenceCards for current section, ChatMessages (last 20 by created_at)
   - Returns: `list[dict]` of `{"role": ..., "content": ...}` messages ready for OpenRouter
   - System prompt structure per design spec Section 5

   **c) `stream_chat()` — proxy OpenRouter SSE**
   - Takes: `model, messages` (assembled), async generator yielding bytes
   - `async with httpx.AsyncClient()` then `client.stream("POST", openrouter_url, json=payload, headers=...)`
   - Yields `response.aiter_bytes()` chunks
   - Also accumulates full response text by parsing `data:` lines for `choices[0].delta.content`
   - Returns accumulated text after stream completes (for persistence)
   - On upstream error before first yield: raise HTTPException
   - On mid-stream error: yield error SSE event `data: {"error": "..."}\n\n` then return

4. **Create `backend/app/routers/chat.py`**

   Three endpoints:

   **a) `GET /chat/models`**
   - No auth required (public endpoint, models list is not sensitive)
   - Calls `chat_service.get_models()`
   - Returns `list[ChatModelInfo]`

   **b) `POST /courses/{course_id}/chat`**
   - Auth: `user_id: str = Depends(get_current_user)`
   - DB: `session: SessionDep`
   - Body: `ChatRequest`
   - Flow:
     1. Persist user message to `ChatMessage` (role="user", model=None)
     2. Call `assemble_context(course_id, body.section_context, session)`
     3. Create async generator that calls `stream_chat(body.model, messages)`
     4. After stream completes, persist assistant message to DB
     5. Return `StreamingResponse(generator, media_type="text/event-stream")`
   - **Note on persistence after stream:** Use a wrapper generator that accumulates content, then persists in a finally block or via FastAPI `BackgroundTask`
   - **Error handling:** If OpenRouter stream errors mid-response, persist user message but discard partial assistant response

   **c) `GET /courses/{course_id}/chat`**
   - Auth: `user_id: str = Depends(get_current_user)`
   - Query params: `limit: int = 50`, `before: str | None = None` (cursor: message ID)
   - Loads messages: `SELECT * FROM chat_messages WHERE course_id=X AND user_id=Y AND (id < before if cursor) ORDER BY created_at DESC LIMIT limit`
   - Returns `list[ChatMessageResponse]`

5. **Register router in `backend/app/main.py`**

   ```python
   from app.routers import chat
   app.include_router(chat.router, prefix="/api")
   ```

6. **Add `.env` vars**

   Add to `backend/.env`:
   ```
   CHAT_DEFAULT_MODEL=anthropic/claude-sonnet-4
   ```

   Add to `backend/.env.example`:
   ```
   CHAT_DEFAULT_MODEL=anthropic/claude-sonnet-4
   ```

### Verification

- [ ] `curl http://localhost:8000/api/chat/models` returns JSON array of models
- [ ] `curl -X POST http://localhost:8000/api/courses/{id}/chat` with auth returns SSE stream
- [ ] `curl http://localhost:8000/api/courses/{id}/chat` with auth returns message history
- [ ] Backend tests pass: `cd backend && uv run pytest`
- [ ] No hardcoded API keys in code (grep for `sk-`)

---

## Phase 3: Frontend — Mermaid Rendering

**Goal:** Add `MermaidBlock` component, integrate into markdown rendering on learn page.

### Tasks

1. **Install mermaid**

   ```bash
   cd frontend && npm install mermaid
   ```

2. **Create `frontend/src/components/MermaidBlock.tsx`**

   Component requirements:
   - `'use client'` directive
   - Module-level `mermaid.initialize({ startOnLoad: false, theme: 'dark' })`
   - `useEffect` + `useRef` pattern with `mermaid.render(id, definition)` returning `{ svg }`
   - Render SVG string via ref (mermaid output is trusted local library output)
   - Consider adding DOMPurify sanitization if mermaid definitions could originate from untrusted input
   - Error fallback: show raw definition in red pre block
   - Props: `{ definition: string }`
   - Counter-based unique IDs for multiple diagrams on same page

3. **Update learn page markdown rendering**

   In `frontend/src/app/courses/[id]/learn/page.tsx`:
   - Import `MermaidBlock`
   - Add `components` prop to `<ReactMarkdown>`:
   ```tsx
   <ReactMarkdown
     components={{
       code({ className, children }) {
         if (/language-mermaid/.test(className || ''))
           return <MermaidBlock definition={String(children).replace(/\n$/, '')} />;
         return <code className={className}>{children}</code>;
       },
     }}
   >
     {currentSection.content || 'Content not yet generated.'}
   </ReactMarkdown>
   ```

### Verification

- [ ] Create a test course section with a mermaid code block manually in the DB. Load the learn page — diagram renders as SVG.
- [ ] Invalid mermaid syntax shows error text, does not crash the page.
- [ ] `cd frontend && npx next build` compiles without errors.

---

## Phase 4: Frontend — Chat Drawer

**Goal:** Build the ChatDrawer component with model selector, streaming messages, and conversation history.

### Tasks

1. **Add new types to `frontend/src/lib/types.ts`**

   ```typescript
   export interface ChatMessage {
     id: string;
     role: 'user' | 'assistant';
     content: string;
     model: string | null;
     section_context: number;
     created_at: string;
   }

   export interface ChatModel {
     id: string;
     name: string;
     context_length: number;
     pricing_prompt: string;
     pricing_completion: string;
   }
   ```

2. **Add API functions to `frontend/src/lib/api.ts`**

   **a) `getChatModels()`** — `GET /api/chat/models`, returns `ChatModel[]`

   **b) `getChatHistory(courseId, token?, before?)`** — `GET /api/courses/{id}/chat?before={cursor}`, returns `ChatMessage[]`

   **c) `sendChatMessage(courseId, message, model, sectionContext, token?)`** — returns raw `Response` (not JSON) so the component can read the SSE stream:
   ```typescript
   export async function sendChatMessage(
     courseId: string, message: string, model: string,
     sectionContext: number, token?: string | null
   ): Promise<Response> {
     return fetch(`${API_BASE}/api/courses/${courseId}/chat`, {
       method: 'POST',
       headers: authHeaders(token),
       body: JSON.stringify({ message, model, section_context: sectionContext }),
     });
   }
   ```

3. **Create `frontend/src/components/ChatDrawer.tsx`**

   This is the largest new component. Structure:

   **Props:**
   ```typescript
   interface ChatDrawerProps {
     courseId: string;
     currentSectionPosition: number;
     currentSectionTitle: string;
   }
   ```

   **State:**
   - `open`, `messages`, `input`, `streaming`, `streamingContent`
   - `models`, `selectedModel`, `modelPickerOpen`, `modelSearch`
   - `messagesEndRef` for auto-scroll

   **Effects:**
   - On mount: fetch models via `getChatModels()`, set default model from `NEXT_PUBLIC_CHAT_DEFAULT_MODEL` or first model
   - On first open: load history via `getChatHistory(courseId, token)`

   **Send handler:**
   1. Add user message to local state immediately (optimistic)
   2. Set `streaming = true`, `streamingContent = ''`
   3. Call `sendChatMessage()` — get raw Response
   4. Read `response.body` as ReadableStream via `getReader()`
   5. Parse SSE lines: extract `data:` prefixed lines, parse JSON, get `choices[0].delta.content`
   6. Append content chunks to `streamingContent` via state setter
   7. On `[DONE]`: add full assistant message to `messages`, clear streaming state

   **Model picker:** Dropdown overlay filtered by `modelSearch`. Shows: model name, context length, pricing.

   **Markdown in messages:** Use `<ReactMarkdown>` with same `components={{ code }}` pattern for Mermaid in chat.

   **Layout per approved mockup:**
   - Collapsed: floating pill bottom-right (`fixed bottom-4 right-6`)
   - Expanded: `fixed bottom-0 left-0 right-0 h-[40vh]` with transition
   - Messages auto-scroll to bottom

4. **Integrate ChatDrawer into learn page**

   In `frontend/src/app/courses/[id]/learn/page.tsx`:
   - Import `ChatDrawer`
   - Add below the content area:
   ```tsx
   <ChatDrawer
     courseId={courseId}
     currentSectionPosition={currentSection.position}
     currentSectionTitle={currentSection.title}
   />
   ```

5. **Add env var to frontend**

   In `frontend/.env.local` (or `.env`):
   ```
   NEXT_PUBLIC_CHAT_DEFAULT_MODEL=anthropic/claude-sonnet-4
   ```

### Verification

- [ ] Click "Ask AI" pill — drawer opens
- [ ] Type message, send — see streaming response appear token by token
- [ ] Model picker shows models from OpenRouter, can switch
- [ ] Close and reopen drawer — previous messages load from history
- [ ] Mermaid diagrams in chat responses render as SVGs
- [ ] Navigate to different section — `section_context` updates
- [ ] `cd frontend && npx next build` compiles without errors

---

## Phase 5: Writer Agent Mermaid Prompt Update

**Goal:** Update writer agent to include Mermaid diagrams in section content where appropriate.

### Tasks

1. **Update writer system prompt in `backend/app/agent.py`**

   Find the writer agent's system prompt (the `create_writer()` function). Add to the instructions:

   > Where a concept benefits from a visual aid — process flows, architecture diagrams, state transitions, relationship maps, or decision trees — include a Mermaid diagram using a ```mermaid fenced code block. Prefer flowchart (graph TD/LR), sequence, or entity-relationship diagrams. Keep diagrams simple: under 15 nodes, clear labels, no styling directives.
   >
   > Not every section needs a diagram. Use them only when visual representation genuinely aids understanding.

2. **No other backend changes needed** — the writer already outputs markdown, and the frontend now renders Mermaid blocks.

### Verification

- [ ] Generate a new course on a technical topic (e.g., "Docker networking")
- [ ] Check that at least some sections contain mermaid code blocks
- [ ] These blocks render as diagrams on the learn page
- [ ] Existing courses still render correctly (no regressions)

---

## Phase 6: Final Verification

**Goal:** End-to-end verification that all M4 features work together.

### Checklist

- [ ] **Chat flow:** Open learn page, click Ask AI, type question, see streaming response, close/reopen, history persists
- [ ] **Model switching:** Open model picker, search, select different model, send message, response comes from new model
- [ ] **Context awareness:** Ask about a concept from the current section, assistant references section content and evidence
- [ ] **Cross-section references:** Ask about a concept from a prior section, assistant says "As covered in Section N..."
- [ ] **Mermaid in chat:** Ask "Can you diagram how X works?", assistant responds with Mermaid block, renders as SVG
- [ ] **Mermaid in content:** Generate new course, sections contain Mermaid diagrams, render correctly
- [ ] **Error handling:** Send message with invalid model ID, graceful error, no crash
- [ ] **Auth:** Unauthenticated request to chat returns 401
- [ ] **Backend tests:** `cd backend && uv run pytest` passes
- [ ] **Frontend build:** `cd frontend && npx next build` succeeds
- [ ] **No anti-patterns:** Grep checks below return no results

### Anti-Pattern Grep Checks

```bash
grep -r "mermaid.run" frontend/src/
grep -r "aiter_text" backend/app/chat_service.py
grep -r "sk-or-" backend/app/
```

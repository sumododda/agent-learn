# Security Fixes Plan

Based on the full-stack security review of agent-learn (backend, frontend, Trigger.dev worker).

## Phase 0: Reference

### Files to modify

| File | Fixes |
|---|---|
| `backend/app/auth.py` | Audience validation, JWKS timeout, generic error messages |
| `backend/app/routers/internal.py` | Constant-time token comparison, generic error responses |
| `backend/app/routers/courses.py` | Ownership check fix (reject NULL user_id) |
| `backend/app/config.py` | Remove default DB credentials, add CORS_ORIGINS |
| `backend/app/main.py` | Configurable CORS origins, startup validation |
| `backend/app/schemas.py` | Input length validation, Literal status type |
| `frontend/src/components/CitationRenderer.tsx` | URL protocol validation on href |
| `frontend/src/components/EvidencePanel.tsx` | URL protocol validation on href |
| `frontend/src/lib/api.ts` | Add missing `getEvidence` function with auth |
| `trigger/src/lib/api-client.ts` | Fail-fast on missing token, truncate error bodies |
| `.gitignore` | Add `.trigger/` |

### Patterns to follow

- `hmac.compare_digest(a, b)` for constant-time string comparison (Python stdlib)
- `jwt.decode(token, key, algorithms=["RS256"], issuer=..., audience=...)` for audience validation
- `Field(..., min_length=1, max_length=500)` for Pydantic input constraints
- `safeUrl()` helper that rejects non-http/https protocols

---

## Phase 1: Backend Auth & Token Security (High Priority)

### What to implement

1. **`backend/app/auth.py`** — Add `audience` validation to JWT decode:
   - Add `CLERK_AUDIENCE: str = ""` to `config.py` Settings
   - Pass `audience=settings.CLERK_AUDIENCE` to `jwt.decode()` (only if non-empty, to not break dev)
   - Add `timeout=5.0` to the JWKS HTTP request
   - Change error detail from `str(e)` to generic `"Invalid token"`
   - Add try/except around JWKS fetch that falls back to stale cache

2. **`backend/app/routers/internal.py`** — Constant-time token comparison:
   - Import `hmac`
   - Replace `x_internal_token != settings.INTERNAL_API_TOKEN` with `not hmac.compare_digest(x_internal_token, settings.INTERNAL_API_TOKEN)`
   - Change all `detail=str(e)` in exception handlers to generic messages like `"Internal processing error"`, log the real error with `logger.exception()`

3. **`backend/app/routers/courses.py`** — Fix ownership checks:
   - Change all `if course.user_id and course.user_id != user_id:` to `if course.user_id != user_id:`
   - This means NULL user_id courses are no longer accessible (they're legacy dev data anyway)

### Verification checklist

- [ ] `grep -n "compare_digest" backend/app/routers/internal.py` returns a match
- [ ] `grep -n "audience" backend/app/auth.py` returns a match
- [ ] `grep -n "timeout" backend/app/auth.py` returns a match
- [ ] `grep -n "course.user_id and" backend/app/routers/courses.py` returns 0 matches
- [ ] `grep -n "str(e)" backend/app/routers/internal.py` returns 0 matches
- [ ] `cd backend && uv run python -m pytest --tb=short -q` — all tests pass

### Anti-pattern guards

- Do NOT remove the `if course.user_id != user_id` check entirely — it must stay, just without the `and` guard
- Do NOT add audience validation as required if `CLERK_AUDIENCE` is empty — dev environments may not set it

---

## Phase 2: Config & Startup Hardening (Medium Priority)

### What to implement

1. **`backend/app/config.py`**:
   - Change `DATABASE_URL` default to `""` (no hardcoded credentials)
   - Add `CORS_ORIGINS: list[str] = ["http://localhost:3000"]`

2. **`backend/app/main.py`**:
   - Read CORS origins from `settings.CORS_ORIGINS` instead of hardcoded string
   - Add startup event that validates required settings are non-empty: `DATABASE_URL`, `INTERNAL_API_TOKEN`, `CLERK_JWKS_URL`, `CLERK_ISSUER`
   - If any are empty, log a warning (don't crash in dev, but make it visible)

3. **`backend/app/schemas.py`** — Add input constraints:
   - `CourseCreate.topic`: `Field(..., min_length=1, max_length=500)`
   - `CourseCreate.instructions`: `Field(None, max_length=2000)`
   - `RegenerateRequest.overall_comment`: `Field(None, max_length=2000)`
   - `SectionComment.comment`: `Field(..., min_length=1, max_length=2000)`
   - `SetCourseStatusRequest.status`: Change to `Literal["generating", "completed", "completed_partial", "failed"]`
   - `ProgressUpdateRequest.current_section`: `Field(None, ge=0)`
   - `ProgressUpdateRequest.completed_section`: `Field(None, ge=0)`

### Verification checklist

- [ ] `grep -n "max_length" backend/app/schemas.py` returns multiple matches
- [ ] `grep -n "Literal" backend/app/schemas.py` returns a match
- [ ] `grep -n "CORS_ORIGINS" backend/app/main.py` returns a match
- [ ] `grep -n 'agentlearn:agentlearn' backend/app/config.py` returns 0 matches
- [ ] All tests pass

### Anti-pattern guards

- Do NOT make startup validation crash the app — use warnings so dev environments without full config still start
- Do NOT remove the localhost default from CORS_ORIGINS — it's needed for dev

---

## Phase 3: Frontend XSS & Auth Fixes (High Priority)

### What to implement

1. **Create `frontend/src/lib/safe-url.ts`** — URL validation utility:
   ```typescript
   export function safeUrl(url: string): string {
     try {
       const parsed = new URL(url);
       if (['http:', 'https:'].includes(parsed.protocol)) return url;
     } catch { /* invalid URL */ }
     return '#';
   }
   ```

2. **`frontend/src/components/CitationRenderer.tsx`** — Apply `safeUrl()` to all `<a href={...}>` that render `source_url`

3. **`frontend/src/components/EvidencePanel.tsx`** — Apply `safeUrl()` to all `<a href={...}>` that render `source_url`

4. **`frontend/src/lib/api.ts`** — Add missing `getEvidence` function with auth token parameter:
   ```typescript
   export async function getEvidence(courseId: string, sectionPosition?: number, token?: string | null): Promise<EvidenceCard[]> {
     const url = sectionPosition !== undefined
       ? `${API_BASE}/api/courses/${courseId}/evidence?section_position=${sectionPosition}`
       : `${API_BASE}/api/courses/${courseId}/evidence`;
     const res = await fetch(url, { headers: authHeaders(token), cache: 'no-store' });
     if (!res.ok) throw new Error('Failed to fetch evidence');
     return res.json();
   }
   ```

5. **`frontend/src/components/EvidencePanel.tsx`** — Update `getEvidence` call to pass auth token via `useAuth().getToken()`

### Verification checklist

- [ ] `grep -rn "safeUrl" frontend/src/components/` returns matches in CitationRenderer and EvidencePanel
- [ ] `grep -n "getEvidence" frontend/src/lib/api.ts` returns a match
- [ ] `grep -rn "javascript:" frontend/src/` returns 0 matches (no hardcoded javascript: URLs)
- [ ] No TypeScript compilation errors

### Anti-pattern guards

- Do NOT use regex to validate URLs — use `new URL()` which handles edge cases
- Do NOT add `rehype-raw` to markdown rendering (would open XSS)

---

## Phase 4: Trigger.dev Worker Hardening (High Priority)

### What to implement

1. **`trigger/src/lib/api-client.ts`**:
   - Fail fast if `INTERNAL_API_TOKEN` is empty: `if (!INTERNAL_API_TOKEN) throw new Error("INTERNAL_API_TOKEN is required")`
   - Same for `INTERNAL_API_URL` if it's empty
   - Truncate error response bodies to 200 chars in `InternalApiError`
   - Add URL protocol validation on `INTERNAL_API_URL` at module load
   - Add `AbortController` with 60s timeout on fetch calls

2. **Root `.gitignore`** — Add `.trigger/`

3. **`trigger/package.json`**:
   - Align `trigger.dev` devDependency to `4.4.3` (match SDK)
   - Change dev script from `npx trigger.dev@latest dev` to `trigger.dev dev`

### Verification checklist

- [ ] `grep -n "throw.*INTERNAL_API_TOKEN" trigger/src/lib/api-client.ts` returns a match
- [ ] `grep -n "slice(0, 200)" trigger/src/lib/api-client.ts` returns a match
- [ ] `grep -n "AbortController" trigger/src/lib/api-client.ts` returns a match
- [ ] `grep -n ".trigger/" .gitignore` returns a match
- [ ] TypeScript compiles: `cd trigger && npx tsc --noEmit`

### Anti-pattern guards

- Do NOT remove the localhost default for `INTERNAL_API_URL` — it's correct for dev
- Do NOT change the fetch to axios or another library — keep it simple

---

## Phase 5: Verification

### Run all checks

1. `cd backend && uv run python -m pytest --tb=short -q` — all tests pass
2. `cd trigger && npx tsc --noEmit` — TypeScript compiles
3. Grep checks from all phases above
4. Verify no secrets in committed files: `git grep -i "sk_test\|sk_live\|password\|secret" -- ':!*.example' ':!*.md' ':!*.lock'`
5. Verify `.env` files not tracked: `git ls-files '*.env*' | grep -v example` returns empty

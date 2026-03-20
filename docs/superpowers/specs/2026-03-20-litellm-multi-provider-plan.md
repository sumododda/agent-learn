# Implementation Plan: LiteLLM Multi-Provider with Encrypted API Keys

**Date:** 2026-03-20
**Design Spec:** `docs/superpowers/specs/2026-03-20-litellm-multi-provider-design.md`

## Phase 0: Documentation Discovery — Consolidated Findings

### LiteLLM API (Confirmed)

**Async completion:**
```python
from litellm import acompletion

response = await acompletion(
    model="anthropic/claude-sonnet-4-20250514",
    messages=[{"role": "user", "content": "hello"}],
    api_key="sk-...",          # passed directly, not env var
    api_base="...",            # Azure, NVIDIA
    api_version="...",         # Azure
    vertex_ai_project="...",   # Vertex
    vertex_ai_location="...", # Vertex
    vertex_credentials="...", # Vertex (JSON string)
    response_format={"type": "json_object"},  # JSON mode
)
# Returns ModelResponse (OpenAI-compatible)
# Access: response.choices[0].message.content
```

**Async streaming:**
```python
response = await acompletion(model=..., messages=..., stream=True, api_key=...)
async for chunk in response:
    text = chunk.choices[0].delta.content  # may be None
```

**Provider prefixes:** `anthropic/`, `azure/`, `mistral/`, `nvidia_nim/`, `vertex_ai/`, `openrouter/`

**Model listing:** No live per-provider API. Use static curated lists + `litellm.model_cost` dict for metadata. OpenRouter dynamic listing via its `/api/v1/models` endpoint directly.

**Exceptions:** `litellm.AuthenticationError`, `litellm.RateLimitError`, `litellm.BadRequestError`, `litellm.NotFoundError`, `litellm.Timeout`. All have `.status_code`, `.message`, `.llm_provider`.

### Crypto APIs (Confirmed)

**Argon2id key derivation:**
```python
from argon2.low_level import hash_secret_raw, Type

derived_key = hash_secret_raw(
    secret=peppered_password,  # bytes
    salt=salt,                 # 16 bytes from os.urandom
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, type=Type.ID,
)
```

**AES-256-GCM:**
```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

nonce = os.urandom(12)
aesgcm = AESGCM(derived_key)  # 32 bytes
ciphertext = aesgcm.encrypt(nonce, plaintext, None)  # includes 16-byte tag
plaintext = aesgcm.decrypt(nonce, ciphertext, None)   # raises InvalidTag on failure
```

**HMAC pepper:**
```python
import hmac
peppered = hmac.digest(PEPPER, password.encode(), 'sha256')  # 32 bytes
```

**Memory zeroing:** Use `bytearray` + manual zeroing. Best-effort in CPython.

### Codebase Structure (Confirmed)

**deepagents removal required:** `create_deep_agent()` expects LangChain `BaseChatModel`. No way to pass LiteLLM directly. Must replace all 7 agent creators with direct `litellm.acompletion()` calls.

**`_invoke_agent()` pattern (agent_service.py:79-110):** Currently calls `agent.ainvoke({"messages": [...]})` expecting deepagents interface. Will be replaced with direct `litellm.acompletion()` calls.

**Structured output:** Currently uses `ToolStrategy(PydanticModel)` from LangChain. Replace with LiteLLM's `response_format={"type": "json_object"}` + JSON schema in system prompt + Pydantic parsing.

**Pipeline call chain:**
```
courses.py → pipeline.start_pipeline(course_id)
  → run_pipeline(course_id)
    → agent_service.run_discover_and_plan(course_id, session)
    → agent_service.run_research_section(course_id, pos, session)
    → agent_service.run_verify_section(course_id, pos, session)
    → agent_service.run_write_section(course_id, pos, session)
    → agent_service.run_edit_section(course_id, pos, session)
```

All functions need `(provider, model, credentials, extra_fields)` threaded through.

**Anti-patterns to avoid:**
- Do NOT use `litellm.get_model_list()` — does not exist
- Do NOT set `os.environ` for per-user keys — race condition in multi-user
- Do NOT use `ToolStrategy` — LangChain-only
- Do NOT use `base_url` param name — use `api_base` (convention in all LiteLLM docs)

---

## Phase 1: Crypto Module + Database Schema

**Goal:** Build the encryption foundation and database tables. No LLM changes yet.

### Tasks

1. **Create `backend/app/crypto.py`**
   - `derive_key(password: str, salt: bytes, pepper: bytes) -> bytearray` — HMAC(pepper, password) → Argon2id → 32-byte key
   - `encrypt_credentials(key: bytearray, plaintext: str) -> str` — AES-256-GCM, returns base64 `nonce||ciphertext||tag`
   - `decrypt_credentials(key: bytearray, blob: str) -> str` — reverse, raises `InvalidTag` on failure
   - `generate_credential_hint(provider: str, credentials: dict) -> str` — provider-aware hint
   - `_zero_buffer(buf: bytearray) -> None` — best-effort memory zeroing
   - Copy patterns from Phase 0 crypto findings above

2. **Add SQLAlchemy models to `backend/app/models.py`**
   - `ProviderConfig` — id (UUID), user_id (UUID FK→users.id), provider, encrypted_credentials, credential_hint, extra_fields (JSON), is_default, created_at, updated_at. Unique constraint on (user_id, provider).
   - `UserKeySalt` — user_id (UUID FK→users.id, PK), salt (LargeBinary 16)

3. **Create Alembic migration**
   - `alembic revision --autogenerate -m "create_provider_configs_and_user_key_salts"`
   - Verify migration creates both tables with correct types and constraints

4. **Add `ENCRYPTION_PEPPER` to `backend/app/config.py`**
   - New required field: `ENCRYPTION_PEPPER: str = ""`
   - Add to `.env.example`

5. **Add `argon2-cffi` to `backend/requirements.txt`**
   - `cryptography` already present

### Verification
- [ ] `pytest backend/tests/test_crypto.py` — round-trip encrypt/decrypt, pepper changes break decryption, hint generation per provider
- [ ] `alembic upgrade head` succeeds
- [ ] `alembic downgrade -1` succeeds (reversible)

---

## Phase 2: Key Cache + Provider Routes (CRUD)

**Goal:** In-memory credential cache and API endpoints for managing provider configs. No LLM calls yet.

### Tasks

1. **Create `backend/app/key_cache.py`**
   - Module-level dict: `_cache: dict[str, CacheEntry]`
   - `CacheEntry` dataclass: `credentials: dict[str, dict]`, `expires_at: datetime`
   - `populate(user_id, credentials_dict, ttl_seconds)` — store
   - `get(user_id, provider) -> dict | None` — return credentials or None if expired/missing
   - `get_default(user_id) -> tuple[str, dict] | None` — returns (provider, credentials)
   - `clear(user_id)` — remove entry

2. **Create Pydantic schemas in `backend/app/schemas.py`**
   - `ProviderSaveRequest(provider: str, credentials: dict, extra_fields: dict = {}, password: str)`
   - `ProviderUpdateRequest(credentials: dict | None, extra_fields: dict | None, password: str | None)`
   - `ProviderTestRequest(credentials: dict, extra_fields: dict = {})`
   - `ProviderConfigResponse(provider: str, name: str, credential_hint: str, extra_fields: dict, is_default: bool)`
   - `ProviderDefaultRequest(provider: str)`
   - `PasswordChangeRequest(old_password: str, new_password: str)`
   - Update `LoginResponse` to add `provider_keys_loaded: bool`

3. **Create `backend/app/routers/provider_routes.py`**
   - `GET /providers/registry` — return PROVIDERS dict (from provider_service, Phase 3)
   - `GET /providers` — list user's configured providers (hint, extra_fields, is_default)
   - `POST /providers` — validate credentials (defer actual LiteLLM test to Phase 3), encrypt, save
   - `PUT /providers/{provider}` — update, password required only when credentials present
   - `DELETE /providers/{provider}` — remove provider config
   - `POST /providers/{provider}/test` — placeholder (returns 501 until Phase 3)
   - `PUT /providers/default` — set default provider
   - Rate limit: 3/min on POST/PUT with credentials, 10/min on test

4. **Update `backend/app/routers/auth_routes.py`**
   - Login: after JWT creation, load user_key_salt, derive key, decrypt all provider_configs, populate key_cache. Return `provider_keys_loaded` in response.
   - Add `PUT /api/auth/password` — re-encrypt all provider configs with new password-derived key
   - Handle decryption failure gracefully (log warning, return `provider_keys_loaded: false`)

5. **Register router in `backend/app/main.py`**
   - `app.include_router(provider_routes.router, prefix="/api")`

### Verification
- [ ] `pytest backend/tests/test_key_cache.py` — populate/get/clear/TTL/cache-miss
- [ ] `pytest backend/tests/test_provider_routes.py` — full CRUD, password enforcement, hint-only responses
- [ ] `pytest backend/tests/test_provider_auth_flow.py` — login populates cache, password change re-encrypts
- [ ] Manual: POST a provider config, verify encrypted_credentials in DB is not plaintext
- [ ] Manual: Login, verify key_cache populated, GET /providers returns hint not key

---

## Phase 3: Provider Service + LiteLLM Integration

**Goal:** Replace OpenRouter with LiteLLM. Replace deepagents/LangChain with direct LiteLLM calls.

### Tasks

1. **Create `backend/app/provider_service.py`**
   - `PROVIDERS` dict — registry with fields, model_prefix, models list per provider (copy from design spec)
   - `get_provider_registry() -> dict` — returns PROVIDERS for frontend
   - `_build_litellm_params(provider, model, credentials, extra_fields) -> dict` — maps provider config to litellm kwargs
   - `async validate_credentials(provider, credentials, extra_fields) -> bool` — lightweight `acompletion` test call
   - `async completion(provider, model, messages, credentials, extra_fields, **kwargs) -> ModelResponse` — wraps `litellm.acompletion()`
   - `async stream_completion(provider, model, messages, credentials, extra_fields, **kwargs)` — wraps `litellm.acompletion(stream=True)`
   - `list_models(provider, credentials=None, extra_fields=None) -> list[dict]` — static list or dynamic fetch

2. **Rewrite `backend/app/agent.py`**
   - Remove all imports: `langchain`, `deepagents`, `ToolStrategy`
   - Remove `get_model()`
   - Keep all Pydantic schemas (OutlineSection, CourseOutline, etc.)
   - Keep all system prompts
   - Replace agent creators with async functions that call `provider_service.completion()` directly:
     ```python
     async def invoke_planner(topic, instructions, briefs, provider, model, credentials, extra_fields):
         messages = [
             {"role": "system", "content": PLANNER_PROMPT},
             {"role": "user", "content": _build_planner_message(topic, instructions, briefs)},
         ]
         response = await provider_service.completion(
             provider, model, messages, credentials, extra_fields,
             response_format={"type": "json_object"},
         )
         return CourseOutlineWithBriefs.model_validate_json(response.choices[0].message.content)
     ```
   - Pattern: system prompt + user message → acompletion with JSON mode → parse with Pydantic
   - New functions: `invoke_planner()`, `invoke_writer()`, `invoke_discovery_researcher()`, `invoke_section_researcher()`, `invoke_verifier()`, `invoke_editor()`

3. **Update `backend/app/agent_service.py`**
   - Remove `_invoke_agent()` helper
   - Remove all `create_*()` calls
   - Update all functions to accept `(provider, model, credentials, extra_fields)` params
   - Call new `agent.invoke_*()` functions directly
   - Updated signatures:
     - `discover_topic(topic, instructions, provider, model, credentials, extra_fields)`
     - `generate_outline(topic, instructions, provider, model, credentials, extra_fields)`
     - `run_discover_and_plan(course_id, session, provider, model, credentials, extra_fields)`
     - `run_research_section(course_id, pos, session, provider, model, credentials, extra_fields)`
     - `run_verify_section(course_id, pos, session, provider, model, credentials, extra_fields)`
     - `run_write_section(course_id, pos, session, provider, model, credentials, extra_fields)`
     - `run_edit_section(course_id, pos, session, provider, model, credentials, extra_fields)`

4. **Update `backend/app/pipeline.py`**
   - `start_pipeline(course_id, provider, model, credentials, extra_fields)` — pass credentials into asyncio task
   - `run_pipeline(course_id, provider, model, credentials, extra_fields)` — thread through all sub-calls
   - All `_discover_and_plan`, `_research_section`, `_verify_section`, `_write_section`, `_edit_section` accept and pass credentials

5. **Rewrite `backend/app/chat_service.py`**
   - `get_models(provider, credentials=None, extra_fields=None)` — calls `provider_service.list_models()`
   - `stream_chat(provider, model, messages, credentials, extra_fields)` — calls `provider_service.stream_completion()`, converts to SSE bytes
   - Remove all `httpx` OpenRouter-specific code
   - Remove `_models_cache` / `_models_lock` (caching moves to provider_service if needed)

6. **Update `backend/app/routers/courses.py`**
   - `POST /courses` — read credentials from key_cache, accept optional `provider`/`model`, pass to `generate_outline()`
   - `POST /courses/{id}/generate` — read credentials from key_cache, pass to `start_pipeline()`
   - `POST /courses/{id}/regenerate` — same pattern
   - Add guard: if no credentials in cache and no provider configured → 400 `no_provider_configured`

7. **Update `backend/app/routers/chat.py`**
   - `GET /chat/models` — add `get_current_user` dependency, read default provider from cache, call `get_models()`
   - `POST /courses/{id}/chat` — read credentials from key_cache, pass to `stream_chat()`

8. **Update provider_routes.py**
   - `POST /providers/{provider}/test` — now calls `provider_service.validate_credentials()` (replaces 501 placeholder)
   - `GET /providers/registry` — calls `provider_service.get_provider_registry()`

9. **Update `backend/app/config.py`**
   - Remove: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `CHAT_DEFAULT_MODEL`
   - Keep: `ENCRYPTION_PEPPER`, `DATABASE_URL`, `JWT_SECRET_KEY`, `JWT_EXPIRE_MINUTES`, `TAVILY_API_KEY`

10. **Update `backend/requirements.txt`**
    - Add: `litellm`
    - Remove: `langchain`, `langchain-openai`, `deepagents`

### Verification
- [ ] `pytest` — all existing tests updated and passing (mock `litellm.acompletion`)
- [ ] `grep -r "openrouter" backend/` — no hardcoded OpenRouter references except in provider registry
- [ ] `grep -r "langchain\|deepagents\|init_chat_model\|create_deep_agent" backend/` — zero hits
- [ ] `grep -r "OPENROUTER_API_KEY\|OPENROUTER_MODEL\|CHAT_DEFAULT_MODEL" backend/app/` — zero hits (only in .env.example for reference)
- [ ] Manual: configure Anthropic provider via API, create a course, verify LiteLLM is called
- [ ] Manual: chat with a course using configured provider

---

## Phase 4: Frontend Settings Page

**Goal:** Build the `/settings` page with dynamic provider forms.

### Tasks

1. **Add provider types to `frontend/src/lib/types.ts`**
   - `ProviderField { key, label, type, required, secret, placeholder? }`
   - `ProviderDefinition { name, model_prefix, fields: ProviderField[], models }`
   - `ProviderConfig { provider, name, credential_hint, extra_fields, is_default }`

2. **Add provider API functions to `frontend/src/lib/api.ts`**
   - `getProviderRegistry(token)` → `GET /api/providers/registry`
   - `getProviders(token)` → `GET /api/providers`
   - `saveProvider(data, token)` → `POST /api/providers`
   - `updateProvider(provider, data, token)` → `PUT /api/providers/{provider}`
   - `deleteProvider(provider, token)` → `DELETE /api/providers/{provider}`
   - `testProvider(provider, data, token)` → `POST /api/providers/{provider}/test`
   - `setDefaultProvider(provider, token)` → `PUT /api/providers/default`

3. **Create `frontend/src/app/settings/page.tsx`**
   - Provider list (left side): cards per provider showing status, hint, default badge
   - Dynamic form (right side): fields from registry, rendered by type (password, text, textarea)
   - "Test Connection" button → calls test endpoint, shows success/error
   - "Save" button → opens password modal → calls save endpoint
   - "Remove" button → confirm dialog → calls delete endpoint
   - "Set as Default" toggle
   - Password prompt modal component

4. **Update `frontend/src/context/AuthContext.tsx`**
   - Parse `provider_keys_loaded` from login response
   - Store in context: `providerKeysLoaded: boolean`
   - Expose via hook

5. **Update `frontend/src/app/layout.tsx`**
   - Add "Settings" link to navigation

6. **Add provider guard to course creation**
   - In the homepage / course creation flow: check if user has any configured providers
   - If not, redirect to `/settings` with message

7. **Update chat model selector**
   - `GET /chat/models` now requires auth token — update `getChatModels()` call
   - Handle case where no provider is configured

### Verification
- [ ] Navigate to `/settings` — see all 6 providers listed
- [ ] Click a provider — see correct dynamic form fields
- [ ] Enter test credentials, click "Test Connection" — see success/failure
- [ ] Save with password — verify hint shows, credentials not visible
- [ ] Set as default — verify badge updates
- [ ] Remove a provider — verify it's removed
- [ ] With no providers: try to create course → redirected to settings
- [ ] Frontend build: `npm run build` passes with no errors

---

## Phase 5: Test Suite Update

**Goal:** Update all existing tests and add new test files for the full feature.

### Tasks

1. **New test files:**
   - `test_crypto.py` — key derivation round-trip, encrypt/decrypt, pepper, salt regeneration, hint generation, InvalidTag on wrong key
   - `test_key_cache.py` — populate, get, clear, TTL eviction, cache miss
   - `test_provider_service.py` — registry fields, _build_litellm_params per provider, validate_credentials mock
   - `test_provider_routes.py` — full CRUD, password enforcement, rate limits, test endpoint
   - `test_provider_auth_flow.py` — login populates cache, password change re-encrypts, decryption failure handling

2. **Update existing tests:**
   - `test_pipeline.py` — mock `litellm.acompletion` instead of old agent mocks, pass credentials params
   - `test_error_handling.py` — update agent invocation mocks
   - `test_courses.py` — update `create_course` / `generate` to pass provider/model/credentials
   - `test_auth.py` — update login response to include `provider_keys_loaded`
   - `test_chat.py` (if exists) — update model listing and streaming mocks

3. **Test config:**
   - Add `ENCRYPTION_PEPPER=test-pepper-for-testing` to test environment
   - Use low Argon2 params in tests: `time_cost=1, memory_cost=1024, parallelism=1`

### Verification
- [ ] `pytest` — all tests pass (target: 0 failures)
- [ ] `pytest --tb=short -q` — clean output, no warnings about deprecated imports
- [ ] No test imports `langchain`, `deepagents`, or `openrouter`

---

## Phase 6: Cleanup — Environment Files + Dead Code

**Goal:** Remove all unwanted env vars, .env.local files, and dead references across the entire repo.

### Tasks

1. **Backend `.env` cleanup**
   - Remove from `backend/.env`: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `CHAT_DEFAULT_MODEL`
   - Add to `backend/.env`: `ENCRYPTION_PEPPER` (generate a secure random value)
   - Update `backend/.env.example` to reflect new required vars only

2. **Frontend `.env.local` cleanup**
   - Delete `frontend/.env.local` if it only contains stale Clerk/Trigger.dev keys
   - Or remove stale entries: any `NEXT_PUBLIC_CLERK_*`, `NEXT_PUBLIC_TRIGGER_*` vars
   - Remove `NEXT_PUBLIC_CHAT_DEFAULT_MODEL` if no longer needed (model comes from backend)
   - Update `frontend/.env.example` (or `.env.local.example`) to reflect current needs

3. **Find and delete ALL unwanted `.env.local` files in the repo**
   - `find . -name ".env.local" -type f` — review each one
   - Delete any that contain only stale/unused variables

4. **Sweep for dead env references**
   - `grep -r "OPENROUTER_API_KEY\|OPENROUTER_MODEL\|CHAT_DEFAULT_MODEL" .` — should be zero
   - `grep -r "CLERK_\|TRIGGER_" .` — should be zero (removed in prior migration)
   - `grep -r "NEXT_PUBLIC_CHAT_DEFAULT_MODEL" .` — remove if no longer used
   - `grep -r "langchain\|deepagents\|init_chat_model\|create_deep_agent" .` — should be zero

5. **Remove dead dependencies**
   - Verify `langchain`, `langchain-openai`, `deepagents` are not in `requirements.txt`
   - Verify no frontend packages reference Clerk or Trigger.dev

6. **Clean up docker-compose.yml**
   - Remove any env vars that reference deleted backend vars

7. **Final `.gitignore` check**
   - Ensure `.env`, `.env.local` are in `.gitignore` for both backend and frontend

### Verification
- [ ] `grep -rn "OPENROUTER_API_KEY\|OPENROUTER_MODEL\|CHAT_DEFAULT_MODEL\|CLERK_\|TRIGGER_" . --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yml" --include="*.env*"` — zero results (excluding .git)
- [ ] `find . -name ".env.local" -not -path "./.git/*"` — only files that are intentionally kept
- [ ] `cat backend/.env.example` — only current vars listed
- [ ] `npm run build` — frontend builds
- [ ] `pytest` — all tests pass
- [ ] `git diff --stat` — review total changes

---

## Phase Summary

| Phase | Goal | Key Files | Depends On |
|-------|------|-----------|------------|
| 1 | Crypto + DB schema | crypto.py, models.py, migration | — |
| 2 | Key cache + Provider CRUD API | key_cache.py, provider_routes.py, auth_routes.py | Phase 1 |
| 3 | LiteLLM + deepagents removal | provider_service.py, agent.py, agent_service.py, pipeline.py, chat_service.py | Phase 2 |
| 4 | Frontend settings page | settings/page.tsx, api.ts, types.ts, AuthContext.tsx | Phase 3 |
| 5 | Test suite update | test_*.py | Phase 3-4 |
| 6 | Env cleanup + dead code removal | .env, .env.local, .env.example, docker-compose.yml | Phase 5 |

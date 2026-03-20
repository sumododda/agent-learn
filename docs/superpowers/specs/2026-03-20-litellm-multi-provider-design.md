# LiteLLM Multi-Provider Support with Password-Encrypted API Keys

**Date:** 2026-03-20
**Status:** Draft

## Overview

Replace the hardcoded OpenRouter integration with LiteLLM as a library, supporting 6 providers (Anthropic, Azure OpenAI, Mistral, NVIDIA NIM, Vertex AI, OpenRouter). Users configure provider credentials via a `/settings` UI. API keys are encrypted at rest using AES-256-GCM with password-derived keys (Argon2id + server pepper). No server-side default keys — every user brings their own.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Deployment model | Hybrid — no server default, every user brings own keys | Simplifies trust model, no shared key abuse |
| Key visibility | Write-only (show `****1234` hint) | More secure, keys can be replaced but never read back |
| Providers | Anthropic, Azure, Mistral, NVIDIA, Vertex AI, OpenRouter | User-specified initial set |
| UI forms | Dynamic per provider (provider-specific fields) | Full UX, no env-var workarounds |
| Encryption | AES-256-GCM with Argon2id KDF + server pepper | OWASP best practice, password-derived |
| Key recovery | None — password reset deletes encrypted keys | Acceptable: users re-enter API keys (2 min) |
| LiteLLM integration | Library (in-process) with thin adapter | No proxy overhead, per-user keys passed directly |
| Credential caching | In-memory session cache populated at login | Password only entered at login, not per-action |
| Background jobs | Credentials passed in-memory at job start | If server restarts mid-job, job fails and user retries |

## Database Schema

### `provider_configs` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | PK |
| `user_id` | UUID, FK → users.id | Real foreign key to `users.id` (UUID type) |
| `provider` | Text | `anthropic`, `azure`, `mistral`, `nvidia`, `vertex_ai`, `openrouter` |
| `encrypted_credentials` | Text | Base64: `nonce \|\| ciphertext \|\| tag` |
| `credential_hint` | Text | Provider-aware hint (see below) |
| `extra_fields` | JSON | Non-secret config: endpoint URL, region, project ID, etc. |
| `is_default` | Boolean | User's preferred provider |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

Unique constraint: `(user_id, provider)`.

**Note on `user_id` type:** The new tables use `UUID` with a real `ForeignKey("users.id")`, matching `User.id: Mapped[uuid.UUID]`. Existing tables (`courses`, `learner_progress`, `chat_messages`) store `user_id` as `Text` with no FK constraint — normalizing those is out of scope for this change.

**Credential hint logic:** For API key fields, show last 4 chars (e.g. `****1234`). For Vertex AI service account JSON, show the `client_email` field truncated (e.g. `****@project.iam.gserviceaccount.com`). The hint generation is provider-aware, implemented in `crypto.py`.

### `user_key_salts` table

| Column | Type | Notes |
|--------|------|-------|
| `user_id` | UUID, FK → users.id | PK, real foreign key |
| `salt` | LargeBinary (16 bytes) | CSPRNG-generated, for Argon2id KDF |

One salt per user. Regenerated on password change (all keys re-encrypted).

## Encryption Design

### Key Derivation

```
user_password + ENCRYPTION_PEPPER (env var)
    → HMAC-SHA256(pepper, password)
    → Argon2id(memory=64MiB, iterations=3, parallelism=4, salt=user_salt)
    → 256-bit encryption key
```

The server pepper is HMAC'd with the password before Argon2id. A DB-only breach cannot brute-force without the pepper.

### Parameters

```
KDF:         Argon2id
memory_cost: 65536 KiB (64 MiB)
time_cost:   3
parallelism: 4
hash_length: 32 bytes (256 bits)
salt_length: 16 bytes (CSPRNG)

Cipher:      AES-256-GCM
nonce_length: 12 bytes (CSPRNG)
tag_length:  16 bytes
```

### ENCRYPTION_PEPPER Rotation

Not supported in this phase. If the pepper changes (env var rotated or leaked), all encrypted credentials become unrecoverable. Recovery follows the same path as password reset: delete all `provider_configs` and `user_key_salts` rows; users re-enter their API keys.

### Encrypt Flow (save provider credentials)

1. User submits credentials + account password on settings page
2. Derive encryption key: HMAC(pepper, password) → Argon2id(salt) → 256-bit key
3. Generate 12-byte random nonce
4. AES-256-GCM encrypt the credential JSON (api_key and any secret fields)
5. Store as base64: `nonce || ciphertext || tag`
6. Generate provider-aware `credential_hint`
7. Store non-secret fields (api_base, region, etc.) as plain JSON in `extra_fields`
8. Zero encryption key from memory

### Decrypt Flow (at login)

1. User logs in with email + password
2. If user has no `user_key_salts` row, skip decryption (new user or reset user)
3. Derive encryption key from password + salt + pepper
4. Attempt to decrypt all provider configs for this user
5. If decryption succeeds: populate in-memory session cache, set `provider_keys_loaded: true` in login response
6. If decryption fails (corrupted data, wrong pepper): log warning, set `provider_keys_loaded: false` in login response, do NOT block login
7. Zero encryption key from memory
8. Cache TTL matches JWT expiry (24h)

Login succeeds regardless of key decryption outcome — auth and key decryption are separate concerns.

### Password Change Flow

1. User provides old password + new password
2. Derive old encryption key, decrypt all provider configs
3. Generate new salt
4. Derive new encryption key from new password
5. Re-encrypt all configs with new key
6. Store new salt, update all provider_configs rows
7. Repopulate session cache
8. Atomic DB transaction

### Password Reset Flow

1. Delete all rows from `provider_configs` for this user
2. Delete `user_key_salts` row
3. User re-enters API keys on settings page after reset

## Provider Service (LiteLLM Adapter)

### Provider Registry

New file: `backend/app/provider_service.py`

```python
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "model_prefix": "anthropic/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ],
        "models": [
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-5-20251001",
        ]
    },
    "azure": {
        "name": "Azure OpenAI",
        "model_prefix": "azure/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
            {"key": "api_base", "label": "Endpoint URL", "type": "text", "required": True, "secret": False,
             "placeholder": "https://your-resource.openai.azure.com/"},
            {"key": "api_version", "label": "API Version", "type": "text", "required": True, "secret": False,
             "placeholder": "2024-06-01"},
        ],
        "models": "dynamic"
    },
    "mistral": {
        "name": "Mistral",
        "model_prefix": "mistral/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ],
        "models": [
            "mistral-large-latest",
            "mistral-medium-latest",
            "mistral-small-latest",
            "open-mistral-nemo",
        ]
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "model_prefix": "nvidia_nim/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
            {"key": "api_base", "label": "Base URL", "type": "text", "required": False, "secret": False,
             "placeholder": "https://integrate.api.nvidia.com/v1/"},
        ],
        "models": [
            "meta/llama-3.1-405b-instruct",
            "meta/llama-3.1-70b-instruct",
            "nvidia/nemotron-4-340b-instruct",
        ]
    },
    "vertex_ai": {
        "name": "Vertex AI",
        "model_prefix": "vertex_ai/",
        "fields": [
            {"key": "vertex_credentials", "label": "Service Account JSON", "type": "textarea", "required": True, "secret": True},
            {"key": "vertex_ai_project", "label": "Project ID", "type": "text", "required": True, "secret": False},
            {"key": "vertex_ai_location", "label": "Region", "type": "text", "required": True, "secret": False,
             "placeholder": "us-central1"},
        ],
        "models": [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "claude-sonnet-4@20250514",
        ]
    },
    "openrouter": {
        "name": "OpenRouter",
        "model_prefix": "openrouter/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ],
        "models": "dynamic"
    },
}
```

**Model lists:** Most providers use a static curated list (updated in code as new models release). OpenRouter and Azure use `"dynamic"` — OpenRouter fetches from its `/api/v1/models` endpoint; Azure deployments are user-specific and returned from the Azure API. Dynamic model listing requires valid credentials from the key cache.

### LiteLLM Call Mapping

All providers use `litellm.acompletion()` with credentials passed directly (no env vars):

| Provider | LiteLLM params |
|----------|---------------|
| Anthropic | `model="anthropic/{model}", api_key=...` |
| Azure | `model="azure/{deployment}", api_key=..., api_base=..., api_version=...` |
| Mistral | `model="mistral/{model}", api_key=...` |
| NVIDIA | `model="nvidia_nim/{model}", api_key=..., api_base=...` |
| Vertex AI | `model="vertex_ai/{model}", vertex_ai_project=..., vertex_ai_location=..., vertex_credentials=...` |
| OpenRouter | `model="openrouter/{model}", api_key=...` |

### ProviderService Methods

- `get_provider_registry()` → returns PROVIDERS dict for frontend form rendering
- `validate_credentials(provider, credentials, extra_fields)` → lightweight `litellm.acompletion()` test call
- `completion(provider, model, messages, credentials, extra_fields, **kwargs)` → wraps `litellm.acompletion()`
- `stream_completion(...)` → same with `stream=True`
- `list_models(provider, credentials, extra_fields)` → returns static list or fetches dynamic list

## In-Memory Session Cache

New file: `backend/app/key_cache.py`

- Dict structure: `{user_id: {"credentials": {provider: decrypted_creds}, "expires_at": datetime}}`
- `populate(user_id, credentials_dict, ttl)` — called at login after decryption
- `get(user_id, provider)` → decrypted credentials dict or `None`
- `get_default(user_id)` → credentials for the user's default provider
- `clear(user_id)` — called on logout
- Lazy TTL eviction: check `expires_at` on `get()`, return `None` if expired
- Cache miss returns `None` → API returns 401 with `{"detail": "re_auth_required"}` → frontend redirects to login

**Intentional limitations:** The cache is per-process and non-persistent. Server restarts clear all cached credentials — every logged-in user must re-login. Multi-worker deployments (`uvicorn --workers N`) require sticky sessions or a single-worker configuration. Scaling beyond a single process is out of scope for this phase.

## API Endpoints

New router: `backend/app/routers/provider_routes.py` at `/api/providers`

| Method | Path | Auth | Body | Description |
|--------|------|------|------|-------------|
| `GET` | `/providers/registry` | Yes | — | Provider definitions (fields, placeholders) for dynamic forms |
| `GET` | `/providers` | Yes | — | User's configured providers (name, hint, extra_fields, is_default — never decrypted keys) |
| `POST` | `/providers` | Yes | `{provider, credentials, extra_fields, password}` | Validate via test call, encrypt, save |
| `PUT` | `/providers/{provider}` | Yes | `{credentials?, extra_fields?, password?}` | Update config. `password` required only when `credentials` is present (re-encryption needed). Updating only `extra_fields` does not require password. |
| `DELETE` | `/providers/{provider}` | Yes | — | Remove a provider config |
| `POST` | `/providers/{provider}/test` | Yes | `{credentials, extra_fields}` | Test credentials without saving. Credentials are used transiently for a single validation call and are not persisted or cached. HTTPS required in production. |
| `PUT` | `/providers/default` | Yes | `{provider}` | Set default provider |

**Rate limiting:** `POST /providers` and `PUT /providers/{provider}` (when credentials present) run Argon2id at 64 MiB — rate limit these at `3/minute` to prevent DoS. `POST /providers/{provider}/test` at `10/minute`.

### Pydantic Schemas

New schemas in `backend/app/schemas.py`:

- `ProviderRegistryResponse` — provider definitions with fields
- `ProviderConfigResponse` — provider name, hint, extra_fields, is_default (never credentials)
- `ProviderSaveRequest` — provider, credentials dict, extra_fields dict, password
- `ProviderUpdateRequest` — optional credentials, optional extra_fields, optional password
- `ProviderTestRequest` — credentials dict, extra_fields dict
- `ProviderDefaultRequest` — provider name
- `PasswordChangeRequest` — old_password, new_password
- `LoginResponse` (updated) — token, user_id, provider_keys_loaded (boolean)

### Changes to Existing Endpoints

- `POST /api/courses` — reads credentials from key cache (no password needed). Accepts optional `provider` and `model` fields; defaults to user's default provider with first model in its list.
- `POST /api/courses/{id}/generate` — same, credentials from cache. Accepts optional `provider` and `model`.
- `POST /api/courses/{id}/chat` — same, credentials from cache
- `GET /api/chat/models` — now authenticated (requires `get_current_user`). Returns models for the user's default provider: static list from registry, or dynamic fetch using credentials from cache.
- `POST /api/auth/login` — after JWT creation, derives encryption key, decrypts all provider configs, populates key cache. Response includes `provider_keys_loaded: boolean`.
- `POST /api/auth/register` — no change (new user has no provider configs yet)

### New Auth Endpoints

- `PUT /api/auth/password` — old_password + new_password, re-encrypts all provider configs
- `POST /api/auth/reset-password` — deletes all provider configs and key salt

### Guard Behavior

When a user with no configured providers tries to create a course or chat:
- API returns 400 with `{"detail": "no_provider_configured"}`
- Frontend redirects to `/settings` with explanation message

When `provider_keys_loaded` is `false` after login:
- Frontend shows a banner on `/settings`: "Could not load your provider keys. You may need to re-enter them."

## Frontend

### Settings Page (`/settings`)

**Route:** `/settings`

**Layout:**
- Provider list on the left (or tabs) — one entry per provider from registry
- Dynamic form on the right for the selected provider

**Provider card (list view):**
- Provider name
- Status: "Configured" (green) or "Not configured" (gray)
- Hint: `****1234` if configured
- Default badge if active

**Provider form (detail view):**
- Fields rendered dynamically from `GET /providers/registry`
- Secret fields: password inputs (masked)
- Non-secret fields: text inputs with placeholders
- "Test Connection" button → `POST /providers/{provider}/test`
- "Save" button → password prompt modal → `POST /providers`
- "Remove" button → confirm dialog → `DELETE /providers/{provider}`
- "Set as Default" toggle

**Password prompt modal:**
- Appears when saving/updating credentials
- "Enter your account password to encrypt your API keys"
- Small note: "API keys are encrypted with your password. If you reset your password, you'll need to re-enter your keys."

**Empty state guard:**
- When user tries to create a course with no providers configured
- Redirect to `/settings` with banner: "Configure an LLM provider to get started"

**Decryption failure banner:**
- When `provider_keys_loaded: false` in login response
- Show on `/settings`: "Could not load your saved provider keys. You may need to re-enter them."

### Navigation

- Add "Settings" link to the app navigation/header

## Integration Changes

### deepagents / LangChain Migration

The current `agent.py` uses both `deepagents` (`create_deep_agent`) and `langchain` (`init_chat_model`). The `deepagents` framework expects LangChain `BaseChatModel` objects.

**Strategy:** Replace both `deepagents` and `langchain` with direct LiteLLM calls. The agent creators (`create_planner`, `create_writer`, etc.) will be rewritten to use `litellm.acompletion()` with structured output via LiteLLM's `response_format` parameter. This eliminates the dependency chain: `deepagents → langchain → openai`.

### Pipeline Credential Threading

Modified function signatures for credential passing (passed as parameters, not read from cache, for testability):

```python
# pipeline.py
async def start_pipeline(course_id: str, provider: str, model: str, credentials: dict, extra_fields: dict):
    """Entry point — called from courses router with credentials from key cache."""
    ...

async def run_pipeline(course_id: str, provider: str, model: str, credentials: dict, extra_fields: dict):
    """Orchestrates discover → plan → research → verify → write → edit."""
    ...

async def _discover_and_plan(course_id: str, provider: str, model: str, credentials: dict, extra_fields: dict):
    ...

async def _research_section(course_id: str, section_pos: int, provider: str, model: str, credentials: dict, extra_fields: dict):
    ...

async def _verify_section(course_id: str, section_pos: int, provider: str, model: str, credentials: dict, extra_fields: dict):
    ...

async def _write_section(course_id: str, section_pos: int, provider: str, model: str, credentials: dict, extra_fields: dict):
    ...

async def _edit_section(course_id: str, section_pos: int, provider: str, model: str, credentials: dict, extra_fields: dict):
    ...
```

Each function passes `provider`, `model`, `credentials`, and `extra_fields` to `ProviderService.completion()`. Credentials live only in the asyncio task's memory.

### TAVILY_API_KEY

`TAVILY_API_KEY` remains a server-side env var. Tavily is used for web research (discovery and section research) and does not go through LiteLLM. No change needed.

### Files Modified

| File | Change |
|------|--------|
| `backend/app/config.py` | Remove `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `CHAT_DEFAULT_MODEL`. Add `ENCRYPTION_PEPPER` |
| `backend/app/agent.py` | Rewrite agent creators to use `ProviderService.completion()` instead of deepagents/langchain |
| `backend/app/chat_service.py` | Replace OpenRouter model listing with provider-aware listing via registry + LiteLLM |
| `backend/app/pipeline.py` | All functions accept `(provider, model, credentials, extra_fields)` params |
| `backend/app/agent_service.py` | Agent service functions accept and pass through credentials |
| `backend/app/models.py` | Add `ProviderConfig` and `UserKeySalt` models |
| `backend/app/schemas.py` | Add provider and auth Pydantic schemas (see list above) |
| `backend/app/main.py` | Register provider_routes router |
| `backend/app/routers/auth_routes.py` | Login populates key cache; add password change endpoint; LoginResponse includes `provider_keys_loaded` |
| `backend/app/routers/courses.py` | Read credentials from cache, pass to pipeline. Accept optional `provider`/`model` in create/generate. |
| `backend/app/routers/chat.py` | `GET /chat/models` becomes authenticated; `POST /chat` reads credentials from cache |
| `backend/requirements.txt` | Add `litellm`, `argon2-cffi`. Remove `langchain`, `langchain-openai`, `deepagents` |
| `frontend/src/app/settings/page.tsx` | New settings page |
| `frontend/src/lib/api.ts` | Add provider API calls |
| `frontend/src/lib/types.ts` | Add provider types |
| `frontend/src/context/AuthContext.tsx` | Handle `provider_keys_loaded` in login response, store in context |
| `frontend/src/app/layout.tsx` | Add Settings nav link |

### Files Created

| File | Purpose |
|------|---------|
| `backend/app/provider_service.py` | LiteLLM adapter with provider registry |
| `backend/app/key_cache.py` | In-memory session cache for decrypted credentials |
| `backend/app/crypto.py` | Argon2id key derivation + AES-256-GCM encrypt/decrypt + provider-aware hint generation |
| `backend/app/routers/provider_routes.py` | Provider CRUD API endpoints |
| `backend/alembic/versions/xxx_create_provider_tables.py` | Migration for provider_configs and user_key_salts |
| `frontend/src/app/settings/page.tsx` | Settings page component |

### Dependencies

**Add:**
- `litellm` — unified LLM provider interface
- `argon2-cffi` — Argon2id key derivation

**Remove:**
- `langchain` — replaced by litellm
- `langchain-openai` — replaced by litellm
- `deepagents` — replaced by direct litellm calls (see deepagents migration section)

**Keep:**
- `cryptography` — already installed, provides AES-256-GCM
- `passlib[bcrypt]` — still used for password hashing (login auth)
- `PyJWT` — still used for JWT tokens

## Testing Strategy

### Unit Tests

- `test_crypto.py` — Argon2id derivation, AES-256-GCM encrypt/decrypt round-trip, pepper integration, salt regeneration, provider-aware hint generation
- `test_provider_service.py` — registry returns correct fields, credential mapping to LiteLLM params per provider, static/dynamic model list logic
- `test_key_cache.py` — populate, get, clear, TTL eviction, cache miss returns None

### Integration Tests

- `test_provider_routes.py` — CRUD for provider configs, password required on save (not for extra_fields-only update), hint returned (never full key), test connection endpoint, rate limiting
- `test_provider_auth_flow.py` — login populates cache and returns `provider_keys_loaded`, logout clears cache, re-login after restart, password change re-encrypts, decryption failure returns `provider_keys_loaded: false` without blocking login

### Mocking

- `litellm.acompletion` mocked in all tests (no real provider API calls)
- Crypto tests use real Argon2id/AES with test-friendly params (low memory/iterations for speed)

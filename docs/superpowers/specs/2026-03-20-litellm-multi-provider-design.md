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
| `user_id` | FK → users | |
| `provider` | Text | `anthropic`, `azure`, `mistral`, `nvidia`, `vertex_ai`, `openrouter` |
| `encrypted_credentials` | Text | Base64: `nonce \|\| ciphertext \|\| tag` |
| `credential_hint` | Text | Last 4 chars of the API key, e.g. `****1234` |
| `extra_fields` | JSON | Non-secret config: endpoint URL, region, project ID, etc. |
| `is_default` | Boolean | User's preferred provider |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

Unique constraint: `(user_id, provider)`.

### `user_key_salts` table

| Column | Type | Notes |
|--------|------|-------|
| `user_id` | FK → users | PK |
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

### Encrypt Flow (save provider credentials)

1. User submits credentials + account password on settings page
2. Derive encryption key: HMAC(pepper, password) → Argon2id(salt) → 256-bit key
3. Generate 12-byte random nonce
4. AES-256-GCM encrypt the credential JSON (api_key and any secret fields)
5. Store as base64: `nonce || ciphertext || tag`
6. Store `credential_hint` = last 4 chars of api_key
7. Store non-secret fields (api_base, region, etc.) as plain JSON in `extra_fields`
8. Zero encryption key from memory

### Decrypt Flow (at login)

1. User logs in with email + password
2. Derive encryption key from password + salt + pepper
3. Decrypt all provider configs for this user
4. Populate in-memory session cache: `{user_id: {provider: credentials}}`
5. Zero encryption key from memory
6. Cache TTL matches JWT expiry (24h)

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
        ]
    },
    "mistral": {
        "name": "Mistral",
        "model_prefix": "mistral/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ]
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "model_prefix": "nvidia_nim/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
            {"key": "api_base", "label": "Base URL", "type": "text", "required": False, "secret": False,
             "placeholder": "https://integrate.api.nvidia.com/v1/"},
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
        ]
    },
    "openrouter": {
        "name": "OpenRouter",
        "model_prefix": "openrouter/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ]
    },
}
```

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

## In-Memory Session Cache

New file: `backend/app/key_cache.py`

- Dict structure: `{user_id: {"credentials": {provider: decrypted_creds}, "expires_at": datetime}}`
- `populate(user_id, credentials_dict, ttl)` — called at login after decryption
- `get(user_id, provider)` → decrypted credentials dict or `None`
- `get_default(user_id)` → credentials for the user's default provider
- `clear(user_id)` — called on logout
- Lazy TTL eviction: check `expires_at` on `get()`, return `None` if expired
- Cache miss returns `None` → API returns 401 with `{"detail": "re_auth_required"}` → frontend redirects to login

## API Endpoints

New router: `backend/app/routers/provider_routes.py` at `/api/providers`

| Method | Path | Auth | Body | Description |
|--------|------|------|------|-------------|
| `GET` | `/providers/registry` | Yes | — | Provider definitions (fields, placeholders) for dynamic forms |
| `GET` | `/providers` | Yes | — | User's configured providers (name, hint, extra_fields, is_default — never decrypted keys) |
| `POST` | `/providers` | Yes | `{provider, credentials, extra_fields, password}` | Validate via test call, encrypt, save |
| `PUT` | `/providers/{provider}` | Yes | `{credentials?, extra_fields?, password}` | Update provider config, re-encrypt if credentials change |
| `DELETE` | `/providers/{provider}` | Yes | — | Remove a provider config |
| `POST` | `/providers/{provider}/test` | Yes | `{credentials, extra_fields}` | Test credentials without saving |
| `PUT` | `/providers/default` | Yes | `{provider}` | Set default provider |

### Changes to Existing Endpoints

- `POST /api/courses` — reads credentials from key cache (no password needed)
- `POST /api/courses/{id}/generate` — same, credentials from cache
- `POST /api/courses/{id}/chat` — same, credentials from cache
- `GET /api/chat/models` — returns models for user's default provider from cache
- `POST /api/auth/login` — after JWT creation, derives encryption key, decrypts all provider configs, populates key cache
- `POST /api/auth/register` — no change (new user has no provider configs yet)

### New Auth Endpoints

- `PUT /api/auth/password` — old_password + new_password, re-encrypts all provider configs
- `POST /api/auth/reset-password` — deletes all provider configs and key salt

### Guard Behavior

When a user with no configured providers tries to create a course or chat:
- API returns 400 with `{"detail": "no_provider_configured"}`
- Frontend redirects to `/settings` with explanation message

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

### Navigation

- Add "Settings" link to the app navigation/header

## Integration Changes

### Files Modified

| File | Change |
|------|--------|
| `backend/app/config.py` | Remove `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `CHAT_DEFAULT_MODEL`. Add `ENCRYPTION_PEPPER` |
| `backend/app/agent.py` | Replace `get_model()` with LiteLLM-based functions accepting credentials param |
| `backend/app/chat_service.py` | Replace OpenRouter model listing with provider-aware listing via LiteLLM |
| `backend/app/pipeline.py` | `start_pipeline()` receives credentials from cache, passes through call chain |
| `backend/app/models.py` | Add `ProviderConfig` and `UserKeySalt` models |
| `backend/app/main.py` | Register provider_routes router |
| `backend/app/routers/auth_routes.py` | Login populates key cache; add password change endpoint |
| `backend/requirements.txt` | Add `litellm`, `argon2-cffi`. Remove `langchain`, `langchain-openai` |
| `frontend/src/app/settings/page.tsx` | New settings page |
| `frontend/src/lib/api.ts` | Add provider API calls |
| `frontend/src/lib/types.ts` | Add provider types |
| `frontend/src/app/layout.tsx` | Add Settings nav link |

### Files Created

| File | Purpose |
|------|---------|
| `backend/app/provider_service.py` | LiteLLM adapter with provider registry |
| `backend/app/key_cache.py` | In-memory session cache for decrypted credentials |
| `backend/app/crypto.py` | Argon2id key derivation + AES-256-GCM encrypt/decrypt |
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

**Keep:**
- `cryptography` — already installed, provides AES-256-GCM
- `passlib[bcrypt]` — still used for password hashing (login auth)
- `PyJWT` — still used for JWT tokens

## Testing Strategy

### Unit Tests

- `test_crypto.py` — Argon2id derivation, AES-256-GCM encrypt/decrypt round-trip, pepper integration, salt regeneration
- `test_provider_service.py` — registry returns correct fields, credential mapping to LiteLLM params per provider
- `test_key_cache.py` — populate, get, clear, TTL eviction, cache miss returns None

### Integration Tests

- `test_provider_routes.py` — CRUD for provider configs, password required on save, hint returned (never full key), test connection endpoint
- `test_provider_auth_flow.py` — login populates cache, logout clears cache, re-login after restart, password change re-encrypts

### Mocking

- `litellm.acompletion` mocked in all tests (no real provider API calls)
- Crypto tests use real Argon2id/AES with test-friendly params (low memory/iterations for speed)

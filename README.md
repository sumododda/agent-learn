# agent-learn

An AI-powered learning platform that generates structured, source-grounded courses tailored to a learner's topic, goals, and current level.

## Features

- Course generation from a topic and optional learner instructions
- Approval-driven outline flow before expensive generation begins
- Grounded research pipeline: multi-provider web search, evidence cards, citation tracking
- Real-time generation feed via SSE — sections become available as they complete
- Context-aware follow-up chat scoped to the learner's course
- Persistent progress tracking, course history, and return sessions

## Tech Stack

| Layer          | Choice                                                         |
|----------------|----------------------------------------------------------------|
| Frontend       | Next.js 16 (App Router), React 19, Tailwind CSS 4, shadcn/ui  |
| Backend        | Python FastAPI, SQLAlchemy, asyncpg                            |
| Database       | PostgreSQL 16                                                  |
| Auth           | Custom JWT (iss/aud claims, bcrypt, OTP email, Turnstile)      |
| AI/LLM         | LangChain + OpenRouter                                         |
| Search         | Tavily, Exa, Brave, Serper, DuckDuckGo (multi-provider)        |
| Background     | Custom pipeline worker (research → verify → write → edit)      |
| Real-time      | Server-Sent Events (SSE)                                       |
| Email          | Resend                                                         |
| Security       | AES-256-GCM credential encryption, rate limiting, CSP headers  |
| Deployment     | GKE (Google Kubernetes Engine)                                 |

## Architecture

The frontend makes API calls to the FastAPI backend. Course generation runs in a separate worker process that executes a sequential pipeline (research, verify, write, edit) and streams progress to clients via SSE. All state is persisted in PostgreSQL.

```
+------------------+        +-------------------+        +-------------------+
|                  |        |                   |        |                   |
|   Next.js        +------->+   FastAPI Backend +------->+   PostgreSQL 16   |
|   Frontend       |  HTTP  |   (REST + SSE)    |  SQL   |                   |
|                  |        |                   |        +-------------------+
+------------------+        +--------+----------+
                                      |
                             +--------v----------+        +-------------------+
                             |                   |        |                   |
                             |   Pipeline Worker +------->+   External APIs   |
                             |   (research/write)|        |   (OpenRouter,    |
                             |                   |        |    Search, Resend)|
                             +-------------------+        +-------------------+
```

## Getting Started

### Prerequisites

- Node.js 20+
- Python 3.12+
- PostgreSQL 16
- A `.env` file in `backend/` (see `backend/.env.example` if present)

### Clone and install

```bash
git clone https://github.com/your-org/agent-learn.git
cd agent-learn
```

Install frontend dependencies:

```bash
cd frontend
npm install
```

Install backend dependencies:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure environment

Copy and fill in the backend environment file:

```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your database URL, API keys, JWT secrets, etc.
```

Key variables: `DATABASE_URL`, `OPENROUTER_API_KEY`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `JWT_SECRET_KEY`, `TURNSTILE_SECRET_KEY`, `ENCRYPTION_PEPPER`.

### Start the database

```bash
# Using Docker:
docker run -d --name agent-learn-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres postgres:16
```

### Run migrations

```bash
cd backend
alembic upgrade head
```

### Run in development

Start the backend:

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Start the frontend:

```bash
cd frontend
npm run dev
```

The frontend is available at `http://localhost:3000` and the API at `http://localhost:8000`.

## Project Structure

```
agent-learn/
├── frontend/               # Next.js 16 app
│   ├── app/                # App Router pages and layouts
│   ├── components/         # React components (shadcn/ui based)
│   └── lib/                # API clients, utilities
├── backend/                # FastAPI application
│   ├── routers/            # API route handlers
│   ├── models/             # SQLAlchemy ORM models
│   ├── schemas/            # Pydantic request/response schemas
│   ├── services/           # Business logic
│   ├── pipeline/           # Course generation worker
│   └── alembic/            # Database migrations
├── deploy/                 # GKE deployment manifests and configs
└── README.md
```

## Deployment

Kubernetes manifests and deployment configuration for GKE are located in the `deploy/` directory. The frontend and backend are deployed as separate workloads. See `deploy/` for service definitions, ingress configuration, and environment setup.

## License

MIT

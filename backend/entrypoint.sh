#!/bin/sh
# Build DATABASE_URL with properly encoded password
if [ -n "$POSTGRES_PASSWORD" ]; then
  ENCODED_PW=$(python3 -c "import urllib.parse,os; print(urllib.parse.quote(os.environ['POSTGRES_PASSWORD'], safe=''))")
  if [ -n "$CLOUD_SQL_INSTANCE" ]; then
    export DATABASE_URL="postgresql+asyncpg://agentlearn:${ENCODED_PW}@/agentlearn?host=/cloudsql/${CLOUD_SQL_INSTANCE}"
  else
    DB_HOST="${DB_HOST:-postgres}"
    DB_PORT="${DB_PORT:-5432}"
    export DATABASE_URL="postgresql+asyncpg://agentlearn:${ENCODED_PW}@${DB_HOST}:${DB_PORT}/agentlearn"
  fi
fi
exec "$@"

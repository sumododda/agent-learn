#!/bin/sh
# Construct DATABASE_URL from secrets injected by Cloud Run
if [ -n "$POSTGRES_PASSWORD" ] && [ -n "$CLOUD_SQL_INSTANCE" ]; then
  export DATABASE_URL="postgresql+asyncpg://agentlearn:${POSTGRES_PASSWORD}@/agentlearn?host=/cloudsql/${CLOUD_SQL_INSTANCE}"
fi
exec "$@"

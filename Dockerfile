FROM postgres:16-bookworm AS pgclient
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/lib/postgresql/16/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libssl3 liblz4-1 libzstd1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 bot
COPY --from=pgclient /usr/lib/postgresql/16/bin/pg_dump /usr/lib/postgresql/16/bin/pg_dump
COPY --from=pgclient /usr/lib/postgresql/16/bin/pg_restore /usr/lib/postgresql/16/bin/pg_restore

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
USER bot
CMD ["python", "-m", "app.main"]

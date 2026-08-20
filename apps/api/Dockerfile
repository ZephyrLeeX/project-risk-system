#
# Shared production image for the FastAPI API, Celery worker and the ADR 0030
# scheduler process. The per-service command is selected in docker-compose;
# this image only installs the locked application and its production deps.
# NestJS / Prisma are never part of this image (reference-only legacy stack).

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pin uv to the version that generated uv.lock for reproducible installs.
RUN pip install --no-cache-dir "uv==0.11.32"

# Install locked dependencies first (cacheable layer), without the project.
COPY pyproject.toml uv.lock ./
COPY README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application source, Alembic migration tree and finish the install.
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# Non-root runtime; the storage volume inherits this ownership on first mount.
RUN useradd --system --uid 1001 --home-dir /app appuser \
    && mkdir -p /app/storage/excel /app/storage/mail \
    && chown -R appuser:appuser /app
USER appuser

# Default: serve the API. Worker/scheduler override the command in compose.
EXPOSE 3000
CMD ["uvicorn", "risk_platform.main:app", "--host", "0.0.0.0", "--port", "3000"]

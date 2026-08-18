# AI Agent V2 browser E2E harness

This harness runs against the real browser-facing Compose edge (`proxy → web/api`) and never replaces PostgreSQL, Redis, Celery, SSE, or the FastAPI process with mocks. The E2E-only Compose overlay adds one HTTPS fake vendor and an explicit worker transport injection; it is not part of production Compose.

```sh
./e2e/infra/run.sh
```

`run.sh` creates a fresh PostgreSQL volume, random runtime users/passwords/provider key, TLS certificates, and business/provider fixtures; values are passed only through the temporary process environment and are removed with the Compose stack. It runs fresh Alembic migrations and the browser suite, including the real worker/SSE project-selection journey. A real DeepSeek smoke is a separate gate and remains `BLOCKED_EXTERNAL_INPUTS` when no approved credential is supplied.

# AI Agent V2 browser E2E harness

This harness runs against the real browser-facing Compose edge (`proxy → web/api`) and never replaces PostgreSQL, Redis, Celery, SSE, or the FastAPI process with mocks. Start the approved Compose stack, apply the fresh Alembic head, then run:

```sh
export PATH=/home/lijx/.local/bin:$PATH
pnpm exec playwright install chromium
E2E_BASE_URL=https://localhost:8443 E2E_IGNORE_TLS=true pnpm exec playwright test --config=e2e/playwright.config.ts e2e/agent-v2.spec.ts
```

Set `E2E_USERNAME` and `E2E_PASSWORD` for the authenticated journey. Missing credentials are reported as `BLOCKED_EXTERNAL_INPUTS`; the harness does not invent credentials or report that journey as PASS. A real DeepSeek smoke is a separate gate and must likewise be recorded as `BLOCKED_EXTERNAL_INPUTS` when no approved credential is supplied. The fake DeepSeek wire contract remains covered by the T048 HTTPS adapter tests.

# Browser E2E

Playwright tests target the browser-facing edge and exercise the real web/API contract.

Run against a prepared environment:

```sh
pnpm test:e2e
```

Set `E2E_BASE_URL` for a non-default endpoint and `E2E_IGNORE_TLS=true` only for approved test certificates. JSON results are written under the gitignored `test-results/` directory.

The real DeepSeek smoke remains a separate gate and must stay `BLOCKED_EXTERNAL_INPUTS` when no approved credential is available.

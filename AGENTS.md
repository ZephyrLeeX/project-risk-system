# Repository Guidelines

## Project Structure & Module Organization

The deployable application lives in `project-risk-system/`, a pnpm workspace. `apps/web/src/` contains the Vue 3/Vite UI, organized into views, components, stores, API clients, and shared styles. `apps/api/src/` contains NestJS feature modules; keep controllers, services, DTOs, and policies together by domain. Prisma schema, seeds, and timestamped migrations are under `apps/api/prisma/`. Shared TypeScript contracts belong in `packages/contracts/src/`. Infrastructure and runtime data mounts live in `infra/` and `storage/`. Root-level `docs/`, specifications, `ui-prototype/`, and generated `artifacts/` are reference material; do not treat the prototype as production code.

## Build, Test, and Development Commands

Run commands from `project-risk-system/`:

- `pnpm install` installs all workspace dependencies.
- `pnpm env:init` creates a secure local `.env` without replacing an existing one.
- `pnpm db:up` starts PostgreSQL; `pnpm prisma:generate` generates the client.
- `pnpm --filter @risk-platform/api exec prisma migrate deploy` applies migrations; `pnpm prisma:seed` loads baseline roles and users.
- `pnpm dev` runs the API and web app concurrently; use `pnpm dev:web` or `pnpm dev:api` for one service.
- `pnpm check` runs type checks, Vitest suites, and production builds across the workspace.

## Coding Style & Naming Conventions

Follow `.editorconfig`: UTF-8, LF endings, final newline, two-space indentation, and no trailing whitespace. Use TypeScript with double quotes and trailing commas, matching existing files. Name Vue components and views in PascalCase (`ModalDialog.vue`), TypeScript modules in kebab-case (`risk-timeline-policy.ts`), and classes in PascalCase. Keep API DTO validation at service boundaries and reuse `@risk-platform/contracts` instead of duplicating response types.

## Testing Guidelines

Vitest is used in every workspace package. Co-locate tests as `*.test.ts` beside the implementation. Add focused tests for policies, DTO validation, calculations, guards, and regressions. Run `pnpm test` during development and `pnpm check` before submitting; no numeric coverage threshold is currently configured.

## Commit & Pull Request Guidelines

This snapshot contains no Git history, so no repository-specific commit convention can be inferred. Use concise, imperative, scoped subjects such as `api: validate mailbox credentials`. Keep commits single-purpose. Pull requests should explain behavior and data-model changes, link the relevant issue or specification, list verification commands, call out migrations or environment changes, and include screenshots for visible UI updates.

## Security & Configuration

Never commit `.env`, credentials, imported spreadsheets, mail content, backups, or generated build output. Add configuration keys to `.env.example`, preserve permission and project-scope checks, and ensure sensitive mutations remain audited.

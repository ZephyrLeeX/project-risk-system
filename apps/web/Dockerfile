#
# Production frontend image: build the Vue SPA from source (consuming the
# generated OpenAPI contract from @risk-platform/contracts), then serve the
# static bundle with nginx. The runtime image contains only static assets —
# no Node, no NestJS, no Prisma.

# ---- build the SPA --------------------------------------------------------
# Node 22: pnpm 11.9.0 requires node >=22.13 (within the repo's engines.node <25).
FROM node:22-bookworm-slim AS build
# Install pnpm via npm (bundled with node) rather than corepack: corepack's
# registry fetch is unreliable inside the build container, while npm's own
# downloader is not.
RUN npm install -g pnpm@11.9.0
WORKDIR /app

# Workspace manifests + lockfile for a frozen, filtered install that pulls only
# @risk-platform/web and its workspace dependency @risk-platform/contracts
# (the legacy NestJS app under apps/api is never installed).
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY packages/contracts/package.json packages/contracts/
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile --filter @risk-platform/web...

# Build the contract bundle (dist/index.js consumed by Vite), then the SPA.
COPY packages/contracts packages/contracts
COPY apps/web apps/web
RUN pnpm --filter @risk-platform/contracts build && pnpm --filter @risk-platform/web build

# ---- serve the static bundle ---------------------------------------------
FROM nginx:1.27-alpine AS runtime
COPY infra/proxy/frontend.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
EXPOSE 80

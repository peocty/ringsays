# Enterprise portal: static build served by unprivileged nginx with strict security headers.
# Build context is the repository root (workspace packages are needed). Configuration is read at
# container start (API_BASE, OIDC_AUTHORITY, optional OIDC_CLIENT_ID, OIDC_EXTRA_ORIGINS,
# PORTAL_TIME_ZONE), so one image runs in every environment.
FROM node:22-alpine AS build
WORKDIR /repo
RUN corepack enable
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY packages/domain/package.json packages/domain/
COPY apps/portal/package.json apps/portal/
RUN pnpm install --frozen-lockfile --filter @ringsays/portal...
COPY packages/domain packages/domain
COPY contracts contracts
COPY apps/portal apps/portal
RUN pnpm --filter @ringsays/domain build && pnpm --filter @ringsays/portal build \
 && find apps/portal/dist -name '*.map' -delete

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY apps/portal/nginx/default.conf.template apps/portal/nginx/security-headers.inc.template /etc/nginx/templates/
COPY apps/portal/nginx/15-ringsays.envsh /docker-entrypoint.d/15-ringsays.envsh
COPY --from=build /repo/apps/portal/dist /usr/share/nginx/html
EXPOSE 8080

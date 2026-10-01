# Enterprise portal: static build served by unprivileged nginx with strict security headers.
# Build context is the repository root (workspace packages are needed).
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
ARG VITE_API_BASE
ARG VITE_OIDC_AUTHORITY
ARG VITE_OIDC_CLIENT_ID=ringsays-portal
ENV VITE_API_BASE=$VITE_API_BASE VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID
RUN pnpm --filter @ringsays/domain build && pnpm --filter @ringsays/portal build

FROM nginxinc/nginx-unprivileged:1.27-alpine
ARG VITE_API_BASE
ARG VITE_OIDC_AUTHORITY
COPY apps/portal/nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_ORIGIN=$VITE_API_BASE OIDC_ORIGIN=$VITE_OIDC_AUTHORITY
COPY --from=build /repo/apps/portal/dist /usr/share/nginx/html
EXPOSE 8080

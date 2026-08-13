# syntax=docker/dockerfile:1
# =============================================================================
# Employee Support Copilot — Cloud Run image
#
# Multi-stage build of the Next.js app in agents/employee-support-copilot
# using Next's standalone output (see next.config.js: output: "standalone").
# The final image contains only the self-contained server bundle + static
# assets — no devDependencies, no npm.
#
# Build context MUST be the app directory:
#   docker buildx build --platform linux/amd64 \
#     -f deployment/google/agents/Dockerfile \
#     -t <image> agents/employee-support-copilot
# (normally driven by deploy.sh in this directory)
# =============================================================================

# ── deps: install exactly what the lockfile pins ─────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ── builder: compile the production bundle ───────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# STANDALONE_BUILD=1 opts into Next's standalone output (see next.config.js —
# kept env-gated so Amplify Hosting still gets the default .next layout).
# SEARCHAAS_BASE_URL is read server-side at request time, so no other
# build-time env is needed — the same image can be promoted across backends.
ENV STANDALONE_BUILD=1
RUN npm run build

# ── runner: slim production image ────────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0

RUN addgroup -S nodejs && adduser -S nextjs -G nodejs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 8080

# The standalone server honours PORT (Cloud Run injects 8080).
CMD ["node", "server.js"]

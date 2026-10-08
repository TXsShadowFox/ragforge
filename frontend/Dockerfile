# syntax=docker/dockerfile:1
# The dashboard (Next.js) as a small standalone Node server.
# Build from this folder: docker build -t ragforge-frontend frontend

# ----------------------------------------------------------------- deps ----
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

# ---------------------------------------------------------------- build ----
FROM node:24-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# -------------------------------------------------------------- runtime ----
FROM node:24-alpine AS runtime
WORKDIR /app
# API_URL (where this server finds the API) and PUBLIC_API_URL (the address browsers use,
# shown in the widget's tag) are read when the server runs, so one image fits every setup.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Only the traced files the server needs (output: "standalone"), not all of node_modules.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
# Never run as root inside the container: the image has a "node" user.
USER node
EXPOSE 3000
CMD ["node", "server.js"]

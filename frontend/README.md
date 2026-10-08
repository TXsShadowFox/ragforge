# Dashboard

The RAGForge dashboard (Next.js 16, React 19, TypeScript, Tailwind CSS 4):

- sign up and log in
- **Documents**: upload by drag and drop, with live status (uploaded, processing, ready, failed)
- **Playground**: chat with your documents: streamed answers, sources with pages, cache hits, ratings
- **API keys**: create a secret key (servers) or a public key (the chat widget), shown once; revoke
- **Analytics**: questions per day, answer times (p50, p95), cache hit rate and cost, and the
  answer quality (thumbs up and down, the latest bad answers)

## How it talks to the API

The browser only talks to the dashboard. The dashboard's server keeps the API login
token in an **httpOnly cookie** (page scripts cannot read it) and forwards the browser's
calls: `/api/v1/<path>` goes to the API's `/v1/<path>` with `Authorization: Bearer <token>`.
Uploads and streamed answers pass through without being held in memory.

| File                                | What it does                                                   |
| ----------------------------------- | -------------------------------------------------------------- |
| `src/app/api/v1/[...path]/route.ts` | the proxy to the API (`src/lib/backend.ts`)                    |
| `src/app/api/session/route.ts`      | log in (sets the cookie) and log out                           |
| `src/app/api/signup/route.ts`       | sign up, then log in                                           |
| `src/proxy.ts`                      | pages: no login cookie, go to `/login` (never runs for `/api`) |

Changes (POST, PUT, PATCH, DELETE) must come from the dashboard's own pages (the `Origin`
header), so other websites, even on another port of the same host, cannot act for you.

Pages render in the browser and load their data from `/api/v1`, so the server never
reads the cookie while it renders a page.

## Run it

From the repo root: `make dev` and `make worker` (API and worker), then `make frontend`
(this app at http://localhost:3000). Or everything in Docker: `make up`.

| Setting          | Default                 | What it is                                                     |
| ---------------- | ----------------------- | -------------------------------------------------------------- |
| `API_URL`        | `http://localhost:8000` | where this server finds the API (in Docker: `http://api:8000`) |
| `PUBLIC_API_URL` | `http://localhost:8000` | the API address for browsers, shown in the widget's tag        |

Both are read when the server starts, so one Docker image works everywhere.

## Checks

| Command                                                       | What it does                                                                              |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `npm test`                                                    | unit tests (Vitest): the proxy, the cookie, page redirects, the stream reader, the widget |
| `npm run e2e`                                                 | the whole flow in a real browser (Playwright): needs `make up` and the Groq key           |
| `npm run lint` / `npm run typecheck` / `npm run format:check` | ESLint / TypeScript / Prettier                                                            |

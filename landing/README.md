# AgentLayer Landing

A React + Vite site deployed on Vercel. Server-side onboarding routes live in
`api/` as Vercel Functions; secrets and PostgreSQL access never belong in
`src/`.

## Local Development

```bash
npm install
vercel dev
```

Copy `.env.example` to a local environment file and configure PostgreSQL plus at
least one OAuth provider. `vercel dev` is required when testing `/api/*`; plain
`npm run dev` only starts the Vite frontend.

Apply the Better Auth and welcome onboarding tables:

```bash
npm run db:migrate
```

## Production Build

```bash
npm run build
npm run preview
```

The production-ready static build is generated in `dist/`.

## Agent-facing website responses

Vercel Routing Middleware in `middleware.js` serves Markdown from the same page
URL when a client prefers `Accept: text/markdown`. Browser requests continue to
use the prerendered HTML. Both representations include `Vary: Accept`. Unknown
page URLs requested as Markdown return HTTP 404 with a Markdown body and links
to the docs and sitemap. Unknown `/api/*` URLs return structured JSON errors.

`/openapi.json` documents the website's onboarding API only. It does not
describe the local wallet runtime or the separate hosted MCP services. Existing
onboarding errors retain their `error` code and `message` and now include a
`hint` with a next step. `/skill.md` and `/sitemap.xml` remain public files.

After deployment, verify the final status, headers, and body for both variants:

```bash
curl -sS -L -i -H 'Accept: text/markdown' https://www.agent-layer.tech/
curl -sS -L -i -H 'Accept: text/html' https://www.agent-layer.tech/
curl -sS -L -i -H 'Accept: text/markdown' https://www.agent-layer.tech/a-page-that-does-not-exist
curl -sS -L -i https://www.agent-layer.tech/openapi.json
curl -sS -L -i https://www.agent-layer.tech/api/a-route-that-does-not-exist
```

## Vercel Deployment

The repository already includes `vercel.json` with explicit settings:

- framework: `vite`
- build command: `npm run build`
- output directory: `dist`

### Via the Vercel UI

1. Import the repository into Vercel.
2. Set the Root Directory to `landing`.
3. Vercel will pick up the build settings from `vercel.json`.
4. Click Deploy.

### Via the Vercel CLI

```bash
npm i -g vercel
vercel
vercel --prod
```

If the project is deployed from a monorepo root, run these commands from inside the `antigravity-landing` directory.

## Welcome onboarding backend

The first implementation accepts a successfully authenticated GitHub **or** X
account. It intentionally has no account-age, follower, repository, or activity
thresholds yet.

OAuth callbacks:

- `https://<origin>/api/auth/callback/github`
- `https://<origin>/api/auth/callback/twitter`

Invite binding:

- `POST /api/onboarding/bind-wallet`
- invite in `Authorization: Bearer ...`
- JSON body: `{ "address": "0x..." }`

Production and preview should use separate databases, auth secrets, OAuth apps,
and campaign IDs. Never expose `DATABASE_URL`, OAuth secrets, or
`BETTER_AUTH_SECRET` through a `VITE_` environment variable.

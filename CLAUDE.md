# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## Project

A full-stack **Spotify web app** built with **Next.js**, integrating the real
**Spotify Web API** via OAuth 2.0. Users log in with their Spotify account and
the app reads/controls their library, playlists, and playback.

## Tech Stack

- **Framework:** Next.js (App Router) — handles both UI and API routes
- **Language:** TypeScript
- **Auth:** Spotify OAuth 2.0 (Authorization Code with PKCE), via NextAuth
- **Data source:** Spotify Web API (`https://api.spotify.com/v1`)
- **Styling:** Tailwind CSS
- **Playback:** Spotify Web Playback SDK (requires Premium)

## Conventions

- Use the **App Router** (`app/` directory), not the Pages Router.
- Keep Spotify API calls in a dedicated `lib/spotify.ts` client — never scatter
  raw `fetch` calls to the Spotify API across components.
- Access tokens are short-lived (1h). Always refresh via the refresh token
  rather than forcing re-login.
- **Never commit secrets.** All credentials live in `.env.local` (gitignored).
  Reference them via `process.env`.
- Server Components for data fetching by default; Client Components (`"use client"`)
  only where interactivity/state is needed (player, controls).
- Prefer route handlers under `app/api/` for anything that touches secrets.

## Environment Variables

See `.env.example`. Required:

- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `NEXTAUTH_SECRET`
- `NEXTAUTH_URL` (e.g. `http://localhost:3000`)

## Commands

- `npm run dev` — start dev server (http://localhost:3000)
- `npm run build` — production build
- `npm run start` — run production build
- `npm run lint` — lint

## Notes for Claude

- Do not hardcode tokens, client secrets, or user IDs.
- When adding Spotify features, check the required OAuth **scopes** and update
  the auth config accordingly.
- The Web Playback SDK requires a **Spotify Premium** account and only runs in
  the browser (Client Component).
- See `BUILD_GUIDE.md` for the step-by-step build plan.

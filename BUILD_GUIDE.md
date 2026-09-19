# Build Guide

A step-by-step plan to build this Spotify app (Next.js + Spotify Web API).

---

## Phase 0 — Spotify Developer Setup

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. **Create an app.** Note the **Client ID** and **Client Secret**.
3. Under the app settings, add a **Redirect URI**:
   - `http://localhost:3000/api/auth/callback/spotify`
4. Decide which **scopes** you need. Common ones for this app:
   - `user-read-email`, `user-read-private`
   - `user-library-read`, `user-library-modify`
   - `playlist-read-private`, `playlist-modify-private`
   - `user-read-playback-state`, `user-modify-playback-state`
   - `streaming` (required for the Web Playback SDK)

---

## Phase 1 — Project Scaffold

```bash
npx create-next-app@latest . --typescript --tailwind --app --eslint
npm install next-auth
```

Create `.env.local` (never commit it):

```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
NEXTAUTH_SECRET=run_openssl_rand_base64_32
NEXTAUTH_URL=http://localhost:3000
```

Also create a committed `.env.example` with the same keys and empty values.

---

## Phase 2 — Authentication (OAuth)

1. Configure **NextAuth** with the Spotify provider in
   `app/api/auth/[...nextauth]/route.ts`.
2. Request the scopes from Phase 0 in the provider's `authorization` params.
3. In the `jwt` callback, store `access_token`, `refresh_token`, and
   `expires_at`. Add a **token refresh** helper that renews the access token
   when expired.
4. In the `session` callback, expose the access token to the app.
5. Add a login button and a protected layout. Redirect unauthenticated users
   to sign in.

**Milestone:** You can log in with Spotify and see your profile name/email.

---

## Phase 3 — Spotify API Client

Create `lib/spotify.ts`:

- A `fetchWebApi(endpoint, method, body)` helper that attaches the current
  access token and hits `https://api.spotify.com/v1`.
- Typed wrapper functions: `getMe()`, `getMyPlaylists()`, `getPlaylist(id)`,
  `search(query, type)`, `getPlaybackState()`, etc.
- Centralize error handling (401 → refresh/re-auth, 429 → respect `Retry-After`).

---

## Phase 4 — Core UI

Layout resembling Spotify:

1. **Sidebar** — Home, Search, library / playlist list.
2. **Main view** — routed pages:
   - `/` Home (recently played, featured)
   - `/search` search box + results
   - `/playlist/[id]` playlist detail with track list
3. **Player bar** (bottom) — current track, play/pause, seek, volume.

Fetch data in Server Components where possible; make the player a Client Component.

**Milestone:** You can browse your playlists and view their tracks.

---

## Phase 5 — Playback (Web Playback SDK)

> Requires a Spotify **Premium** account.

1. Load the Web Playback SDK script in a Client Component.
2. Initialize the player with the user's access token; create a device.
3. Wire the player bar controls to transfer playback to this device and
   control play/pause/next/previous/seek.
4. Subscribe to player state to keep the UI in sync.

**Milestone:** You can play a track in-browser and control it.

---

## Phase 6 — Search & Library Features

- Full-text search (tracks, artists, albums, playlists).
- Save/remove tracks (`user-library-modify`).
- Create/edit playlists (`playlist-modify-private`).

---

## Phase 7 — Polish & Deploy

- Loading/skeleton states, error boundaries, empty states.
- Handle token expiry gracefully (silent refresh).
- Responsive layout.
- Deploy to **Vercel**:
  - Add all env vars in the Vercel project settings.
  - Add the production redirect URI to the Spotify dashboard:
    `https://your-domain.vercel.app/api/auth/callback/spotify`.
  - Set `NEXTAUTH_URL` to the production URL.

---

## Testing Checklist

- [ ] Login/logout works and refresh keeps you signed in past 1 hour
- [ ] Playlists load for the logged-in user
- [ ] Search returns results
- [ ] Playback controls work (Premium)
- [ ] No secrets committed to git
- [ ] Production deploy works with correct redirect URIs

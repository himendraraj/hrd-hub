# HRD HUB server

Streams lecture videos from your Telegram channel to Chrome. Chrome asks for byte ranges, the server pulls those chunks from Telegram, and playback starts without downloading the whole file.

## One-time setup
1. Create an app at https://my.telegram.org (API development tools) and note `api_id` and `api_hash`.
2. On your computer: `pip install telethon` then `python login.py`. Log in with the account that is a member of the channel and copy the `TG_SESSION` value it prints.
3. Copy `.env.example` to `.env` and fill it in. `TG_CHANNEL` is optional now; you can add channels from the admin panel instead.

## Run locally
    pip install -r requirements.txt
    set -a; . ./.env; set +a
    uvicorn app:app --port 8000
Open http://localhost:8000 and log in with APP_USER / APP_PASSWORD.

## Deploy
Any host that runs a Docker container works (a small VPS, Railway, Fly.io, Render).
- Build from this folder's `Dockerfile`.
- Set the variables from `.env.example` as environment variables.
- Mount a persistent volume at `/data` (watched marks, notes, covers and progress live in `/data/state.json`).
- Run a single instance. HTTPS is required because Basic Auth sends your password with every request.

## Admin panel
Open `/admin` and log in with `ADMIN_USER` / `ADMIN_PASSWORD` (if you leave them out, the normal login is also the admin login).
- Channels: add by `@username`, link or numeric ID, or pick from the channels your Telegram account has joined. Remove a channel any time.
- Course covers: upload an image per course. It appears on the course card and each lecture row for everyone.
- Everything is saved in `/data` (`channels.json`, `covers/`, `state.json`), so keep that volume.
- Use different passwords for `APP_PASSWORD` (viewers) and `ADMIN_PASSWORD` (you) if you share the site with anyone.

## Notes
- `TG_SESSION` gives full access to your Telegram account. Treat it like a password and never commit it.
- Keep the site private. Only serve content you own or are licensed to use.
- Chrome plays H.264/AAC MP4. MKV, AVI or HEVC files will not play; open those in Telegram.
- If seeking is slow, the MP4 has its index at the end; re-mux with `ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4`.
- Click Sync Telegram in the header after posting new lectures (the list also refreshes every 10 minutes).

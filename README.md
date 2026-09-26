# UC Major Fit — Deployment Guide

Two pieces:
- **backend/** — FastAPI proxy. Holds your Anthropic API key server-side, rate-limits requests, and serves `/api/interest-signals`.
- **frontend/** — the static site (single `index.html`, majors data included). Talks to your backend, never to Anthropic directly.

This split matters: an API key placed in frontend JavaScript is visible to anyone who opens dev tools. The backend exists specifically so your key never reaches the browser.

---

## 1. Deploy the backend (Render — free tier works)

Render is the easiest free option for a small FastAPI app. Railway works basically the same way if you'd rather use that.

1. Push the `backend/` folder to a GitHub repo (or the whole `major-fit-app/` folder — Render lets you point at a subdirectory).
2. Go to [render.com](https://render.com) → New → **Web Service** → connect your repo.
3. Settings:
   - **Root directory:** `backend` (if you pushed the whole project)
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables (Render dashboard → Environment):
   - `ANTHROPIC_API_KEY` — your real key from [console.anthropic.com](https://console.anthropic.com)
   - `ALLOWED_ORIGINS` — leave as `*` for now, you'll lock this down in step 3
   - `RATE_LIMIT` — `30/hour` is a reasonable starting point per visitor
   - `ADMIN_TOKEN` — any long random string; this is what protects your usage dashboard, pick something only you know
5. Deploy. Render gives you a URL like `https://major-fit-api.onrender.com`.
6. Test it: `curl https://your-url.onrender.com/api/health` should return `{"status":"ok"}`.

**Free tier note:** Render's free tier spins down after inactivity and takes ~30-60 seconds to wake up on the next request. Fine for a portfolio project; if that first-request delay bothers real users, a paid tier ($7/mo) removes it.

---

## 2. Deploy the frontend (Vercel or Netlify — both free)

1. Open `frontend/index.html` and change this line near the top of the `<script>` tag:
   ```js
   const API_BASE_URL = window.MAJOR_FIT_API_BASE || "http://localhost:8000";
   ```
   Update the fallback to your actual backend URL from step 1:
   ```js
   const API_BASE_URL = window.MAJOR_FIT_API_BASE || "https://major-fit-api.onrender.com";
   ```
2. Push `frontend/` to GitHub (same repo or a new one — either works).
3. Go to [vercel.com](https://vercel.com) → New Project → import the repo → set root directory to `frontend` → Deploy.
   (Netlify: drag-and-drop the `frontend` folder at [app.netlify.com/drop](https://app.netlify.com/drop) works too, zero config needed since it's a static file.)
4. You'll get a URL like `https://major-fit.vercel.app`.

---

## 3. Lock down CORS (do this before sharing the link widely)

Right now `ALLOWED_ORIGINS=*` means any website could call your backend and spend your API budget. Once you have your real frontend URL:

1. Go back to Render → your backend service → Environment.
2. Set `ALLOWED_ORIGINS` to your actual frontend URL, e.g. `https://major-fit.vercel.app`
3. Redeploy (Render does this automatically when you save an env var).

---

## 4. Set a spending cap (do this before sharing the link at all)

This is the most important step. In the [Anthropic Console](https://console.anthropic.com), under your organization's billing settings, set a monthly spend limit. Given how cheap this app's calls are (a fraction of a cent per uncached search), even a $5-10/month cap gives you a large safety margin while guaranteeing you can never get an unexpectedly large bill.

---

## Local development

Backend:
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # then fill in your real ANTHROPIC_API_KEY
export $(cat .env | xargs)   # or use a tool like python-dotenv / direnv
uvicorn main:app --reload
```

Frontend: just open `frontend/index.html` in a browser — it'll call `http://localhost:8000` by default, matching the local backend above.

---

## Tracking usage

Every search (cached or not) is logged to a local SQLite file (`usage.db`) on the backend, along with an anonymous per-browser ID (generated client-side via `localStorage`, no login, no personal info) so you can distinguish total searches from unique visitors. Two ways to check it:

- **Dashboard (easiest):** visit `https://your-backend-url.onrender.com/api/admin/dashboard?token=YOUR_ADMIN_TOKEN` in a browser. Shows total searches, unique visitors, 14-day windows for both, cache hit rate, and a ranked table of the most common interests.
- **Raw JSON:** same URL but `/api/admin/stats` instead of `/dashboard`, if you want to pull the numbers into something else.

Set `ADMIN_TOKEN` to any long random string in your backend's environment variables — this is what keeps the dashboard private to you. Without it set, the admin endpoints refuse to work at all.

**For a resume line like "used 2,000+ times in 2 weeks by X students":** the dashboard's "Searches, last 14 days" gives you the first number, and "Unique visitors, last 14 days" gives you the second. A defensible way to phrase the tracking method: *"tracked via anonymous session analytics on a self-hosted backend."* That's accurate — it's not third-party analytics, it's a UUID your own server logs — and it avoids overclaiming precision a simple per-browser ID can't guarantee (e.g. the same student on their phone and laptop counts as two visitors; someone clearing browser data resets their count). If you want a more standard-sounding tool name to cite instead of "self-hosted," Plausible or PostHog (both have free tiers) would give you the same numbers under a name recruiters may recognize — but the built-in tracking here is genuinely sufficient and doesn't require juggling another account.

**One limitation worth knowing:** Render's free tier disk is not guaranteed to survive a redeploy (it does survive normal sleep/wake, just not a fresh deploy). That means your usage history could reset if you push a code update — so once you're tracking toward a resume number, avoid redeploying unless you need to. If you want permanent historical data that survives redeploys regardless, that's a sign to eventually move `usage.db` to a small hosted Postgres (Render and Railway both offer a free tier of this too) — not needed to get started.

## What's already handled for you

- **Common interests are free** — "premed," "law," "cs," "business," and 6 others skip the Anthropic call entirely (see `COMMON_INTEREST_CACHE` in `backend/main.py`), both server-side and client-side.
- **Rate limiting** — caps requests per IP per hour (`RATE_LIMIT` env var), so no single visitor (or bot) can run up your bill.
- **Graceful failure** — if the Anthropic call fails or times out, the backend returns empty signals rather than erroring, and the frontend falls back to local keyword matching so the app still works.
- **No API key in the browser** — verified: the frontend only ever talks to your backend, never to `api.anthropic.com` directly.

## Estimated cost at real traffic

With the expanded cache (20+ common majors covering most of what CA transfer students actually search — CS, pre-med, business, psych, biology, engineering, and more) plus Haiku as the model, real cost should be close to zero:

- **Cached search:** $0 — never touches the Anthropic API at all
- **Uncached search** (an unusual or specifically-phrased interest): roughly $0.0003-0.0006 — a tiny classification call on Haiku, not the old full-dataset call

**Worked example for "2,000 searches in 2 weeks":** even a pessimistic assumption of only 70% cache hit rate (likely low, given how broad the cache now is) means ~600 uncached calls at ~$0.0005 each — **about $0.30 total** for the full two weeks. At a more realistic 85-90% hit rate, it's closer to $0.10-0.15. Either way, this comfortably fits inside a $5 monthly spending cap with a lot of room to spare.

## Extending the cache

To add more common interests, edit `COMMON_INTEREST_CACHE` in **both**:
- `backend/main.py`
- `frontend/index.html` (the `COMMON_INTEREST_CACHE` const)

Keep them in sync — the frontend cache saves a network round-trip for common terms even when the backend is fast, and the backend cache is the real cost-saver if the frontend copy ever gets out of sync.

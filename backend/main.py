import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
# How many /api/interest-signals calls a single IP can make per hour.
# This is what protects your API bill from a runaway client or abuse.
RATE_LIMIT = os.environ.get("RATE_LIMIT", "30/hour")

# Usage tracking — logs every search to a local SQLite file so you can see
# total volume and which interests come up most. Protect the dashboard with
# a token only you know; set it as an env var, never commit it.
DB_PATH = os.environ.get("DB_PATH", "usage.db")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY environment variable is required. "
        "Set it in your hosting provider's environment variables — never commit it to code."
    )

# ---------------------------------------------------------------------------
# Known dataset vocabulary (must match the frontend's majors.json exactly)
# ---------------------------------------------------------------------------

KNOWN_DISCIPLINES = [
    "Architecture", "Arts & Humanities", "Business", "Computer Science", "Education",
    "Engineering", "Life Sciences", "Nursing", "Other Health Science",
    "Other/Interdisciplinary", "Pharmacy", "Physical Sciences/Math", "Public Admin",
    "Public Health", "Social Sciences", "Undeclared",
]

# Hand-curated cache for common, high-traffic interests — these skip the
# Anthropic call entirely and cost nothing. Keep this in sync with the
# COMMON_INTEREST_CACHE list in frontend/index.html.
COMMON_INTEREST_CACHE = [
    {
        "aliases": ["premed", "pre-med", "pre med", "pre-medicine", "premedicine", "medicine", "med school", "med", "doctor"],
        "disciplines": ["Life Sciences", "Other Health Science"],
        "keywords": ["biology", "human biology", "physiology", "biochemistry", "biological sciences", "molecular biology"],
    },
    {
        "aliases": ["law", "pre-law", "prelaw", "pre law", "lawyer", "legal", "attorney"],
        "disciplines": ["Social Sciences", "Public Admin"],
        "keywords": ["political science", "legal studies", "philosophy", "criminology", "public policy", "government"],
    },
    {
        "aliases": ["cs", "computer science", "computerscience", "coding", "programming", "software engineering", "software"],
        "disciplines": ["Computer Science"],
        "keywords": ["computer science", "software", "data science", "computer engineering", "information"],
    },
    {
        "aliases": ["business", "biz", "entrepreneurship", "entrepreneur"],
        "disciplines": ["Business"],
        "keywords": ["business administration", "management", "finance", "marketing", "accounting"],
    },
    {
        "aliases": ["psych", "psychology"],
        "disciplines": ["Social Sciences", "Life Sciences"],
        "keywords": ["psychology", "cognitive science", "behavioral science", "neuroscience"],
    },
    {
        "aliases": ["engineering", "engineer"],
        "disciplines": ["Engineering"],
        "keywords": ["engineering", "mechanical", "electrical", "civil", "structural"],
    },
    {
        "aliases": ["electrical engineering", "electrical eng", "electrical & computer engineering", "ece"],
        "disciplines": ["Engineering"],
        "keywords": ["electrical engineering", "electrical", "electronics", "circuits", "power systems"],
    },
    {
        "aliases": ["mechanical engineering", "mech e", "mech eng", "mechanical eng"],
        "disciplines": ["Engineering"],
        "keywords": ["mechanical engineering", "mechanical", "thermodynamics", "robotics", "design"],
    },
    {
        "aliases": ["civil engineering", "civil eng"],
        "disciplines": ["Engineering"],
        "keywords": ["civil engineering", "civil", "structural", "construction", "transportation"],
    },
    {
        "aliases": ["chemical engineering", "chem e", "chem eng"],
        "disciplines": ["Engineering"],
        "keywords": ["chemical engineering", "chemical", "process engineering"],
    },
    {
        "aliases": ["aerospace engineering", "aero engineering", "aeronautical engineering"],
        "disciplines": ["Engineering"],
        "keywords": ["aerospace engineering", "aerospace", "aeronautical", "astronautical"],
    },
    {
        "aliases": ["computer engineering", "comp eng", "computer hardware engineering"],
        "disciplines": ["Engineering", "Computer Science"],
        "keywords": ["computer engineering", "hardware", "embedded systems", "electrical"],
    },
    {
        "aliases": ["biomedical engineering", "bioengineering", "bio engineering", "biomed engineering"],
        "disciplines": ["Engineering"],
        "keywords": ["biomedical engineering", "bioengineering", "biomedical"],
    },
    {
        "aliases": ["industrial engineering", "industrial eng", "systems engineering"],
        "disciplines": ["Engineering"],
        "keywords": ["industrial engineering", "systems engineering", "operations research"],
    },
    {
        "aliases": ["environmental engineering", "environmental eng"],
        "disciplines": ["Engineering"],
        "keywords": ["environmental engineering", "environmental"],
    },
    {
        "aliases": ["materials science", "materials engineering", "materials science and engineering"],
        "disciplines": ["Engineering", "Physical Sciences/Math"],
        "keywords": ["materials science", "materials engineering", "nanoengineering"],
    },
    {
        "aliases": ["nursing", "nurse"],
        "disciplines": ["Nursing", "Other Health Science"],
        "keywords": ["nursing", "health science", "clinical"],
    },
    {
        "aliases": ["art", "design", "arts", "artist"],
        "disciplines": ["Arts & Humanities"],
        "keywords": ["art", "design", "studio art", "art history", "visual arts"],
    },
    {
        "aliases": ["econ", "economics"],
        "disciplines": ["Social Sciences", "Business"],
        "keywords": ["economics", "business economics", "finance"],
    },
    {
        "aliases": ["data science", "datascience", "data", "data analytics", "analytics"],
        "disciplines": ["Computer Science", "Physical Sciences/Math"],
        "keywords": ["data science", "statistics", "analytics", "applied math"],
    },
    {
        "aliases": ["biology", "bio", "life science", "life sciences", "general biology"],
        "disciplines": ["Life Sciences"],
        "keywords": ["biology", "biological sciences"],
    },
    {
        "aliases": ["human biology", "human bio"],
        "disciplines": ["Life Sciences"],
        "keywords": ["human biology", "physiology", "anatomy", "human physiology"],
    },
    {
        "aliases": ["chemistry", "chem"],
        "disciplines": ["Physical Sciences/Math"],
        "keywords": ["chemistry", "biochemistry", "chemical"],
    },
    {
        "aliases": ["physics"],
        "disciplines": ["Physical Sciences/Math"],
        "keywords": ["physics", "applied physics", "astrophysics"],
    },
    {
        "aliases": ["math", "mathematics"],
        "disciplines": ["Physical Sciences/Math"],
        "keywords": ["mathematics", "applied mathematics", "statistics"],
    },
    {
        "aliases": ["sociology", "socio"],
        "disciplines": ["Social Sciences"],
        "keywords": ["sociology", "social science", "social welfare"],
    },
    {
        "aliases": ["communications", "communication", "comms", "media studies", "journalism"],
        "disciplines": ["Arts & Humanities", "Social Sciences"],
        "keywords": ["communication", "media studies", "journalism", "film"],
    },
    {
        "aliases": ["political science", "poli sci", "polisci", "government studies"],
        "disciplines": ["Social Sciences"],
        "keywords": ["political science", "government", "public policy"],
    },
    {
        "aliases": ["education", "teaching", "teacher"],
        "disciplines": ["Education"],
        "keywords": ["education", "child development", "teaching"],
    },
    {
        "aliases": ["public health"],
        "disciplines": ["Public Health", "Other Health Science"],
        "keywords": ["public health", "health science", "epidemiology"],
    },
    {
        "aliases": ["kinesiology", "exercise science", "sports medicine", "athletic training", "physical therapy", "pt"],
        "disciplines": ["Other Health Science", "Life Sciences"],
        "keywords": ["kinesiology", "exercise science", "physiology", "athletic training"],
    },
    {
        "aliases": ["environmental science", "environmental studies", "sustainability"],
        "disciplines": ["Life Sciences", "Physical Sciences/Math"],
        "keywords": ["environmental science", "environmental studies", "ecology", "earth science"],
    },
    {
        "aliases": ["english", "literature", "creative writing"],
        "disciplines": ["Arts & Humanities"],
        "keywords": ["english", "literature", "creative writing", "rhetoric"],
    },
    {
        "aliases": ["history"],
        "disciplines": ["Arts & Humanities"],
        "keywords": ["history", "historical studies"],
    },
    {
        "aliases": ["undecided", "not sure", "dont know", "no idea", "unsure"],
        "disciplines": [],
        "keywords": [],
    },
]


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9\s-]", "", s.lower().strip())


def lookup_common_interest(interest: str) -> Optional[dict]:
    norm = normalize(interest)
    if not norm:
        return None

    best_entry = None
    best_specificity = 0

    for entry in COMMON_INTEREST_CACHE:
        for alias in entry["aliases"]:
            if alias == norm:
                # Exact match always wins outright — no ambiguity to resolve.
                return {"disciplines": list(entry["disciplines"]), "keywords": list(entry["keywords"])}
            if (alias in norm or norm in alias) and len(alias) > best_specificity:
                # Longer/more specific alias wins over a shorter, broader one —
                # e.g. "electrical engineering" should beat generic "engineering".
                best_specificity = len(alias)
                best_entry = entry

    if best_entry:
        return {"disciplines": list(best_entry["disciplines"]), "keywords": list(best_entry["keywords"])}
    return None


def build_system_prompt() -> str:
    return f"""A student is looking for a college major. Their stated interest may be vague (e.g. "something with data" or "pre-med").

Here is the fixed list of broad academic disciplines used in the dataset you're matching against — you must only choose from this exact list, word for word:
{json.dumps(KNOWN_DISCIPLINES)}

1. Pick the 1-3 disciplines from that exact list most relevant to their interest. Be precise — e.g. "pre-med" should map to Life Sciences (and Other Health Science if it fits), never Engineering or Social Sciences just because a major name happens to contain the word "science".
2. Return 4-8 specific keywords or short phrases that would appear inside real major names matching this interest (e.g. for "pre-med": "biology", "human biology", "physiology", "biochemistry").

Respond with ONLY valid JSON, no markdown fences, no preamble: {{"disciplines": ["..."], "keywords": ["...", "..."]}}"""


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interest TEXT NOT NULL,
                normalized TEXT NOT NULL,
                cached INTEGER NOT NULL,
                client_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Backfill-safe: add client_id to a database created before this column existed.
        cols = [row[1] for row in conn.execute("PRAGMA table_info(searches)").fetchall()]
        if "client_id" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN client_id TEXT")
        conn.commit()


def log_search(interest: str, normalized: str, cached: bool, client_id: Optional[str]):
    # Logging failures should never break the actual request.
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO searches (interest, normalized, cached, client_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (interest, normalized, 1 if cached else 0, client_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to log search: {e}")


def get_stats() -> dict:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) AS c FROM searches").fetchone()["c"]
        cached_count = conn.execute("SELECT COUNT(*) AS c FROM searches WHERE cached = 1").fetchone()["c"]
        last_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM searches WHERE created_at >= datetime('now', '-1 day')"
        ).fetchone()["c"]
        last_7d = conn.execute(
            "SELECT COUNT(*) AS c FROM searches WHERE created_at >= datetime('now', '-7 day')"
        ).fetchone()["c"]
        last_14d = conn.execute(
            "SELECT COUNT(*) AS c FROM searches WHERE created_at >= datetime('now', '-14 day')"
        ).fetchone()["c"]
        unique_visitors = conn.execute(
            "SELECT COUNT(DISTINCT client_id) AS c FROM searches WHERE client_id IS NOT NULL AND client_id != ''"
        ).fetchone()["c"]
        unique_visitors_14d = conn.execute(
            """
            SELECT COUNT(DISTINCT client_id) AS c FROM searches
            WHERE client_id IS NOT NULL AND client_id != ''
            AND created_at >= datetime('now', '-14 day')
            """
        ).fetchone()["c"]
        top = conn.execute(
            """
            SELECT normalized, COUNT(*) AS count
            FROM searches
            GROUP BY normalized
            ORDER BY count DESC
            LIMIT 25
            """
        ).fetchall()

    return {
        "total_searches": total,
        "unique_visitors": unique_visitors,
        "unique_visitors_last_14d": unique_visitors_14d,
        "searches_last_24h": last_24h,
        "searches_last_7d": last_7d,
        "searches_last_14d": last_14d,
        "cache_hit_rate": round(cached_count / total, 3) if total else None,
        "top_interests": [{"interest": r["normalized"], "count": r["count"]} for r in top],
    }


def require_admin(token: Optional[str]):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not configured on the server")
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT])

app = FastAPI(title="UC Major Fit API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

init_db()


class InterestRequest(BaseModel):
    interest: str
    client_id: Optional[str] = None


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/interest-signals")
@limiter.limit(RATE_LIMIT)
async def interest_signals(payload: InterestRequest, request: Request):
    interest = (payload.interest or "").strip()
    if not interest:
        raise HTTPException(status_code=400, detail="interest is required")
    if len(interest) > 200:
        raise HTTPException(status_code=400, detail="interest is too long")

    # Free path: common interests never touch the Anthropic API.
    cached = lookup_common_interest(interest)
    log_search(interest, normalize(interest), cached=bool(cached), client_id=payload.client_id)
    if cached:
        return cached

    system = build_system_prompt()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 300,
                    "system": system,
                    "messages": [{"role": "user", "content": interest}],
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Model request timed out")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not reach the model")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Model API error: {resp.status_code}")

    data = resp.json()
    text_block = next((b.get("text") for b in data.get("content", []) if b.get("type") == "text"), None)
    if not text_block:
        raise HTTPException(status_code=502, detail="No text in model response")

    clean = text_block.strip()
    clean = re.sub(r"^```json", "", clean)
    clean = re.sub(r"^```", "", clean)
    clean = re.sub(r"```$", "", clean).strip()

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        # Fail soft: return no signals rather than a broken response —
        # the frontend already falls back to local tokenizing on empty results.
        return {"disciplines": [], "keywords": []}

    disciplines = [d for d in parsed.get("disciplines", []) if d in KNOWN_DISCIPLINES]
    keywords = [str(k).lower().strip() for k in parsed.get("keywords", []) if k]

    return {"disciplines": disciplines, "keywords": keywords}


# ---------------------------------------------------------------------------
# Admin — usage stats. Protected by ADMIN_TOKEN, set that as an env var.
# ---------------------------------------------------------------------------

@app.get("/api/admin/stats")
async def admin_stats(token: Optional[str] = None):
    require_admin(token)
    return get_stats()


@app.get("/api/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(token: Optional[str] = None):
    require_admin(token)
    stats = get_stats()

    rows = "".join(
        f"<tr><td>{i + 1}</td><td>{r['interest']}</td><td>{r['count']}</td></tr>"
        for i, r in enumerate(stats["top_interests"])
    ) or "<tr><td colspan='3'>No searches logged yet</td></tr>"

    cache_pct = f"{stats['cache_hit_rate'] * 100:.0f}%" if stats["cache_hit_rate"] is not None else "—"

    html = f"""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8" />
        <title>Major Fit — Usage</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; background: #142033; color: #FAF6EC; padding: 40px; }}
            h1 {{ font-weight: 600; }}
            .stats {{ display: flex; gap: 24px; margin-bottom: 32px; flex-wrap: wrap; }}
            .stat {{ background: #1d2f4d; padding: 16px 24px; border-radius: 6px; min-width: 140px; }}
            .stat .num {{ font-size: 28px; font-weight: 700; }}
            .stat .label {{ font-size: 12px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; }}
            table {{ border-collapse: collapse; width: 100%; max-width: 600px; background: #FAF6EC; color: #142033; border-radius: 4px; overflow: hidden; }}
            th, td {{ padding: 10px 16px; text-align: left; }}
            th {{ background: #EFE8D8; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }}
            tr:nth-child(even) {{ background: #f2efe4; }}
        </style>
    </head>
    <body>
        <h1>Major Fit — Usage Dashboard</h1>
        <div class="stats">
            <div class="stat"><div class="num">{stats['total_searches']}</div><div class="label">Total searches</div></div>
            <div class="stat"><div class="num">{stats['unique_visitors']}</div><div class="label">Unique visitors</div></div>
            <div class="stat"><div class="num">{stats['searches_last_14d']}</div><div class="label">Searches, last 14 days</div></div>
            <div class="stat"><div class="num">{stats['unique_visitors_last_14d']}</div><div class="label">Unique visitors, last 14 days</div></div>
            <div class="stat"><div class="num">{stats['searches_last_24h']}</div><div class="label">Last 24h</div></div>
            <div class="stat"><div class="num">{stats['searches_last_7d']}</div><div class="label">Last 7 days</div></div>
            <div class="stat"><div class="num">{cache_pct}</div><div class="label">Free (cached) searches</div></div>
        </div>
        <h2>Most common interests</h2>
        <table>
            <thead><tr><th>#</th><th>Interest</th><th>Count</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


"""
pharma_intel.py
---------------
Daily pharma/life-sciences headline fetcher.
- RSS feeds: parsed with feedparser
- CPhI Online: scraped with httpx + BeautifulSoup
- Stores top 5 headlines per source in pharma_intel_cache table
- Runs once per day via APScheduler (started from main.py lifespan)
- get_cached_intel() returns the latest ~40 headlines for enrichment prompts
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

RSS_SOURCES = [
    {"name": "FiercePharma",              "url": "https://www.fiercepharma.com/rss/xml"},
    {"name": "World Pharma News",          "url": "https://www.worldpharmanews.com/?format=feed&type=rss"},
    {"name": "PharmaTimes",               "url": "https://pharmatimes.com/feed"},
    {"name": "PharmaVoice",               "url": "https://pharmavoice.com/feeds/news"},
    {"name": "Pharmaceutical Technology", "url": "https://www.pharmaceutical-technology.com/feed/"},
    {"name": "Economic Times Pharma",     "url": "https://pharma.economictimes.indiatimes.com/rss/topstories"},
    {"name": "Pharmafile",                "url": "https://pharmafile.com/feed"},
    {"name": "STAT News Pharma",          "url": "https://www.statnews.com/category/pharma/feed/"},
]

CPHI_SOURCES = [
    {"name": "CPhI News & Insights", "url": "https://www.cphi-online.com/news-and-insights/all.html"},
    {"name": "CPhI Reports",         "url": "https://www.cphi-online.com/reports/"},
    {"name": "CPhI Webinars",        "url": "https://www.cphi-online.com/webinars/"},
    {"name": "CPhI Podcasts",        "url": "https://www.cphi-online.com/podcasts/"},
    {"name": "CPhI Event Content",   "url": "https://www.cphi-online.com/event-content/"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
MAX_PER_SOURCE = 5
FETCH_TIMEOUT  = 15


async def _fetch_rss(client: httpx.AsyncClient, source: dict) -> list[dict]:
    items = []
    try:
        resp = await client.get(source["url"], headers=HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:MAX_PER_SOURCE]:
            raw_summary = entry.get("summary") or entry.get("description") or ""
            summary_text = BeautifulSoup(raw_summary, "html.parser").get_text(separator=" ").strip()
            summary_text = summary_text[:400] if summary_text else None
            pub = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
            title = entry.get("title", "").strip()
            link  = entry.get("link", source["url"]).strip()
            if title:
                items.append({
                    "source_name": source["name"],
                    "source_url":  link,
                    "headline":    title,
                    "summary":     summary_text,
                    "published_at": pub.isoformat() if pub else None,
                })
        logger.info(f"[pharma_intel] RSS OK: {source['name']} — {len(items)} items")
    except Exception as e:
        logger.warning(f"[pharma_intel] RSS FAIL: {source['name']}: {e}")
    return items


async def _scrape_cphi(client: httpx.AsyncClient, source: dict) -> list[dict]:
    items = []
    try:
        resp = await client.get(source["url"], headers=HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = []
        for article in soup.find_all("article")[:MAX_PER_SOURCE * 2]:
            heading = article.find(["h2", "h3", "h4"])
            if heading:
                title    = heading.get_text(strip=True)
                link_tag = article.find("a", href=True)
                link     = link_tag["href"] if link_tag else source["url"]
                if link.startswith("/"):
                    link = "https://www.cphi-online.com" + link
                p       = article.find("p")
                snippet = p.get_text(strip=True)[:300] if p else None
                if title:
                    candidates.append({"title": title, "link": link, "snippet": snippet})
        if not candidates:
            for card in soup.find_all(class_=lambda c: c and any(k in c for k in ["card","item","article","post"]))[:MAX_PER_SOURCE * 2]:
                heading = card.find(["h2", "h3", "h4", "a"])
                if heading:
                    title    = heading.get_text(strip=True)
                    link_tag = card.find("a", href=True)
                    link     = link_tag["href"] if link_tag else source["url"]
                    if link.startswith("/"):
                        link = "https://www.cphi-online.com" + link
                    if title and len(title) > 10:
                        candidates.append({"title": title, "link": link, "snippet": None})
        seen = set()
        for c in candidates:
            if c["title"] not in seen:
                seen.add(c["title"])
                items.append({
                    "source_name": source["name"],
                    "source_url":  c["link"],
                    "headline":    c["title"],
                    "summary":     c["snippet"],
                    "published_at": None,
                })
            if len(items) >= MAX_PER_SOURCE:
                break
        logger.info(f"[pharma_intel] Scrape OK: {source['name']} — {len(items)} items")
    except Exception as e:
        logger.warning(f"[pharma_intel] Scrape FAIL: {source['name']}: {e}")
    return items


async def refresh_pharma_intel(sb) -> int:
    all_items: list[dict] = []
    async with httpx.AsyncClient() as client:
        rss_results  = await asyncio.gather(*[_fetch_rss(client, s)   for s in RSS_SOURCES])
        cphi_results = await asyncio.gather(*[_scrape_cphi(client, s) for s in CPHI_SOURCES])
        for r in rss_results:  all_items.extend(r)
        for r in cphi_results: all_items.extend(r)

    if not all_items:
        logger.warning("[pharma_intel] No items fetched from any source")
        return 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        await asyncio.to_thread(
            lambda: sb.table("pharma_intel_cache").delete().eq("industry","pharma").gte("fetched_at", today_start).execute()
        )
    except Exception as e:
        logger.warning(f"[pharma_intel] Cache clear error (non-fatal): {e}")

    rows = [
        {
            "source_name":  item["source_name"],
            "source_url":   item["source_url"],
            "headline":     item["headline"],
            "summary":      item["summary"],
            "published_at": item["published_at"],
            "industry":     "pharma",
        }
        for item in all_items
    ]
    try:
        await asyncio.to_thread(lambda: sb.table("pharma_intel_cache").insert(rows).execute())
        logger.info(f"[pharma_intel] Stored {len(rows)} headlines")
    except Exception as e:
        logger.error(f"[pharma_intel] Insert error: {e}")
        return 0
    return len(rows)


def get_cached_intel(sb, industry: str = "pharma", limit: int = 10) -> str:
    try:
        result = (
            sb.table("pharma_intel_cache")
            .select("source_name, headline, summary, source_url, published_at")
            .eq("industry", industry)
            .order("fetched_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"[pharma_intel] get_cached_intel error: {e}")
        return ""

    if not rows:
        return ""

    lines = [
        "### Recent Pharma Industry Intelligence",
        "The following are the latest headlines from leading pharma/life-sciences sources. "
        "Use these to enrich your analysis of the visitor's context and relevance.\n"
    ]
    for row in rows:
        pub = ""
        if row.get("published_at"):
            try:
                dt  = datetime.fromisoformat(row["published_at"])
                pub = f" ({dt.strftime('%b %d, %Y')})"
            except Exception:
                pass
        lines.append(f"**[{row['source_name']}]{pub}** {row['headline']}")
        if row.get("summary"):
            lines.append(f"  → {row['summary']}")
        lines.append(f"  Source: {row['source_url']}\n")
    return "\n".join(lines)


def start_intel_scheduler(sb):
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()

        async def _job():
            logger.info("[pharma_intel] Scheduled refresh starting...")
            count = await refresh_pharma_intel(sb)
            logger.info(f"[pharma_intel] Scheduled refresh done: {count} headlines")

        scheduler.add_job(_job, "cron", hour=6, minute=0)
        scheduler.add_job(_job, "date", run_date=datetime.now(timezone.utc) + timedelta(seconds=30))
        scheduler.start()
        logger.info("[pharma_intel] Scheduler started (daily 06:00 UTC + startup run)")
        return scheduler
    except ImportError:
        logger.warning("[pharma_intel] APScheduler not installed — pip install apscheduler")
        return None

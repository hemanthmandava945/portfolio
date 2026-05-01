"""
Robotics Job Agent
==================
Daily scraper for robotics jobs in Germany matched to Hemanth Mandava's profile.

Sources (RSS / public endpoints — no scraping that gets blocked):
  - LinkedIn Jobs RSS (via rss.app-style public feed)
  - Indeed RSS
  - StepStone RSS
  - Arbeitsagentur public API
  - Greenhouse / Lever / Workable public APIs for target companies
  - EURAXESS RSS

Filters:
  - Keywords match (robotics, ROS, perception, VLA, etc.)
  - Germany location
  - Posted within last N days (default 1)
  - Deduplicated against state.json (jobs already seen)

Output:
  - HTML email digest via SMTP
  - state.json updated with seen job IDs
"""

import os
import sys
import json
import time
import smtplib
import hashlib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup

# ============ CONFIG ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("jobs")

# Profile keywords — a job must match at least one to be included
INCLUDE_KEYWORDS = [
    # core role titles
    "robotics engineer", "robotics software", "ros engineer", "ros2",
    "ros 2", "perception engineer", "autonomy engineer", "slam engineer",
    "robot learning", "computer vision engineer", "manipulation engineer",
    "robotics", "autonomous", "embodied ai", "vla",
    # tech keywords
    "ros", "nav2", "isaac sim", "lerobot", "behavior tree",
    "lidar", "point cloud", "sim-to-real", "sim to real",
    # German equivalents
    "robotik", "roboter", "autonomes", "autonomes fahren",
    "wahrnehmung", "bildverarbeitung",
]

# Strong negative filter — jobs containing these are excluded
EXCLUDE_KEYWORDS = [
    "rpa developer", "process automation analyst", "uipath",
    "blue prism", "automation anywhere",
    # exclude pure manager / sales roles
    "head of sales", "account manager", "sales engineer",
    "recruiter", "talent acquisition", "hr business partner",
]

# Companies whose careers pages we'll hit directly (Greenhouse / Lever public APIs)
GREENHOUSE_COMPANIES = [
    # slug as it appears in their greenhouse URL
    "agilerobots",
    "wandelbots",
    "neura",
    "magazino",
    "frankaemika",
    "kuka",
    "helsing",
    "wayve",
    "vay",
    "sereact",
]

LEVER_COMPANIES = [
    # company slug on jobs.lever.co/<slug>
    "robco",
    "arx-robotics",
]

# Search queries used for aggregator boards
QUERIES = [
    "robotics engineer",
    "ROS 2",
    "perception engineer",
    "autonomous driving",
    "computer vision robotics",
    "robotik ingenieur",
]

LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "2"))
MAX_JOBS_IN_EMAIL = 60
STATE_FILE = Path("state.json")

USER_AGENT = (
    "Mozilla/5.0 (compatible; HemanthJobAgent/1.0; "
    "+https://github.com/hemanthmandava945/portfolio)"
)

# ============ STATE ============

def load_state():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()).get("seen", []))
        except Exception:
            return set()
    return set()

def save_state(seen):
    # Keep only the last 5000 to prevent the file growing forever
    seen_list = list(seen)[-5000:]
    STATE_FILE.write_text(json.dumps({"seen": seen_list, "updated": datetime.now(timezone.utc).isoformat()}, indent=2))

# ============ HELPERS ============

def job_id(title, company, url):
    s = f"{title.lower().strip()}|{company.lower().strip()}|{url.split('?')[0]}"
    return hashlib.sha1(s.encode()).hexdigest()[:16]

def matches_profile(title, description=""):
    text = f"{title} {description}".lower()
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in text for kw in INCLUDE_KEYWORDS)

def in_germany(location_text):
    if not location_text:
        return True  # let it through if unknown — better recall
    loc = location_text.lower()
    de_markers = [
        "germany", "deutschland", "berlin", "munich", "münchen", "hamburg",
        "frankfurt", "stuttgart", "köln", "cologne", "dresden", "leipzig",
        "düsseldorf", "duesseldorf", "hannover", "nürnberg", "nuremberg",
        "bremen", "essen", "dortmund", "augsburg", "karlsruhe", "freiburg",
        "ulm", "ingolstadt", "wolfsburg", "tübingen", "tuebingen",
        "metzingen", "reutlingen", "bonn", "mannheim", "remote eu",
        "remote (germany)", "germany remote", "deutschland remote",
        "remote · germany",
    ]
    return any(m in loc for m in de_markers)

def safe_get(url, timeout=20, **kwargs):
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("User-Agent", USER_AGENT)
    try:
        r = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET failed {url}: {e}")
        return None

# ============ SOURCES ============

def fetch_indeed():
    """Indeed RSS — one query per loop."""
    jobs = []
    for q in QUERIES:
        url = (
            f"https://de.indeed.com/jobs?q={quote_plus(q)}"
            f"&l=Deutschland&fromage={LOOKBACK_DAYS}&sort=date&format=rss"
        )
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "")
            company = ""
            location = ""
            # Indeed RSS embeds company/location in the title: "Title - Company - Location"
            parts = title.split(" - ")
            if len(parts) >= 3:
                title = parts[0]
                company = parts[1]
                location = " - ".join(parts[2:])
            if not in_germany(location):
                continue
            if not matches_profile(title, summary):
                continue
            jobs.append({
                "title": title.strip(),
                "company": company.strip() or "(unknown)",
                "location": location.strip() or "Germany",
                "url": link,
                "source": "Indeed",
                "posted": entry.get("published", ""),
            })
        time.sleep(1)  # be polite
    log.info(f"Indeed: {len(jobs)} matching jobs")
    return jobs

def fetch_arbeitsagentur():
    """German federal employment agency public API."""
    jobs = []
    api = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
    headers = {
        "User-Agent": USER_AGENT,
        # Public client_id used by their official frontend
        "X-API-Key": "jobboerse-jobsuche",
    }
    for q in ["Robotics", "ROS", "Perception", "Autonomous Driving"]:
        params = {
            "was": q,
            "wo": "Deutschland",
            "size": "30",
            "page": "1",
            "veroeffentlichtseit": str(LOOKBACK_DAYS),
        }
        r = safe_get(api, headers=headers, params=params)
        if not r:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        for item in (data.get("stellenangebote") or []):
            title = item.get("titel") or item.get("beruf", "")
            company = item.get("arbeitgeber", "(unknown)")
            location = ""
            ort = item.get("arbeitsort") or {}
            location = ", ".join(filter(None, [ort.get("ort"), ort.get("region"), "Germany"]))
            ref = item.get("refnr", "")
            url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}" if ref else ""
            if not matches_profile(title, item.get("beruf", "")):
                continue
            jobs.append({
                "title": title.strip(),
                "company": company.strip(),
                "location": location.strip(),
                "url": url,
                "source": "Arbeitsagentur",
                "posted": item.get("aktuelleVeroeffentlichungsdatum", ""),
            })
        time.sleep(1)
    log.info(f"Arbeitsagentur: {len(jobs)} matching jobs")
    return jobs

def fetch_greenhouse(slug):
    """Greenhouse public job board API — works for many tech companies."""
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    r = safe_get(api)
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        location = (j.get("location") or {}).get("name", "")
        url = j.get("absolute_url", "")
        # No description in this endpoint, match on title only
        if not matches_profile(title):
            continue
        if not in_germany(location):
            continue
        jobs.append({
            "title": title,
            "company": slug.replace("-", " ").title(),
            "location": location or "Germany",
            "url": url,
            "source": f"Greenhouse ({slug})",
            "posted": j.get("updated_at", ""),
        })
    return jobs

def fetch_lever(slug):
    """Lever public job board API."""
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = safe_get(api)
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    jobs = []
    for j in data:
        title = j.get("text", "")
        cats = j.get("categories", {}) or {}
        location = cats.get("location", "")
        url = j.get("hostedUrl", "")
        if not matches_profile(title):
            continue
        if not in_germany(location):
            continue
        jobs.append({
            "title": title,
            "company": slug.replace("-", " ").title(),
            "location": location or "Germany",
            "url": url,
            "source": f"Lever ({slug})",
            "posted": datetime.fromtimestamp(
                (j.get("createdAt") or 0) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d") if j.get("createdAt") else "",
        })
    return jobs

def fetch_target_companies():
    jobs = []
    for slug in GREENHOUSE_COMPANIES:
        jobs.extend(fetch_greenhouse(slug))
        time.sleep(0.5)
    for slug in LEVER_COMPANIES:
        jobs.extend(fetch_lever(slug))
        time.sleep(0.5)
    log.info(f"Target companies: {len(jobs)} matching jobs")
    return jobs

def fetch_euraxess():
    """EU research jobs RSS."""
    jobs = []
    url = (
        "https://euraxess.ec.europa.eu/jobs/search/rss?"
        "keywords%5B0%5D=robotics&country%5B0%5D=Germany"
    )
    feed = feedparser.parse(url)
    for entry in feed.entries:
        title = entry.get("title", "")
        link = entry.get("link", "")
        summary = entry.get("summary", "")
        if not matches_profile(title, summary):
            continue
        jobs.append({
            "title": title.strip(),
            "company": "(EURAXESS)",
            "location": "Germany",
            "url": link,
            "source": "EURAXESS",
            "posted": entry.get("published", ""),
        })
    log.info(f"EURAXESS: {len(jobs)} matching jobs")
    return jobs

# ============ EMAIL ============

def render_email(jobs, run_date):
    if not jobs:
        return f"<p>No new robotics jobs matched today's filters in Germany. Filter ran on {run_date}.</p>"

    by_source = {}
    for j in jobs:
        by_source.setdefault(j["source"].split(" ")[0], []).append(j)

    css = """
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
             color: #111; line-height: 1.5; margin: 0; padding: 24px; background: #fafafa; }
      .wrap { max-width: 720px; margin: 0 auto; background: #fff; border: 1px solid #e5e5e5; }
      .head { padding: 28px 28px 18px; border-bottom: 1px solid #e5e5e5; }
      h1 { margin: 0 0 6px; font-size: 22px; }
      .meta { color: #666; font-size: 13px; }
      .accent { color: #ff5722; }
      .section { padding: 8px 28px 24px; }
      h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.12em;
           color: #888; border-bottom: 1px solid #eee; padding: 18px 0 6px; margin: 0; }
      .job { padding: 14px 0; border-bottom: 1px solid #f0f0f0; }
      .job:last-child { border-bottom: none; }
      .job .title { font-weight: 600; font-size: 15px; }
      .job .title a { color: #111; text-decoration: none; }
      .job .title a:hover { color: #ff5722; }
      .job .meta-row { color: #666; font-size: 13px; margin-top: 4px; }
      .job .meta-row .sep { color: #ccc; margin: 0 6px; }
      .footer { padding: 18px 28px; background: #f7f7f7; color: #888; font-size: 12px;
                border-top: 1px solid #e5e5e5; }
    """

    sections = []
    for source, items in sorted(by_source.items()):
        rows = []
        for j in items:
            rows.append(f"""
              <div class="job">
                <div class="title"><a href="{j['url']}">{j['title']}</a></div>
                <div class="meta-row">
                  <strong>{j['company']}</strong>
                  <span class="sep">·</span> {j['location']}
                  <span class="sep">·</span> {j.get('posted','')}
                </div>
              </div>
            """)
        sections.append(f"""
          <div class="section">
            <h2>{source} <span class="accent">· {len(items)}</span></h2>
            {''.join(rows)}
          </div>
        """)

    html = f"""
    <html><head><style>{css}</style></head><body>
      <div class="wrap">
        <div class="head">
          <h1>Robotics jobs <span class="accent">·</span> Germany</h1>
          <div class="meta">
            {len(jobs)} new posting{'s' if len(jobs)!=1 else ''} matched on {run_date}
          </div>
        </div>
        {''.join(sections)}
        <div class="footer">
          Filtered for: ROS 2, perception, VLA, autonomous driving, manipulation,
          embodied AI · Lookback: {LOOKBACK_DAYS} day(s) ·
          Sources: Indeed, Arbeitsagentur, Greenhouse, Lever, EURAXESS
        </div>
      </div>
    </body></html>
    """
    return html

def send_email(subject, html_body):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_addr = os.environ.get("TO_EMAIL", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText("View this email in HTML.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, [to_addr], msg.as_string())
    log.info(f"Email sent to {to_addr}")

# ============ MAIN ============

def main():
    seen = load_state()
    log.info(f"Loaded {len(seen)} previously seen jobs")

    all_jobs = []
    for fetcher in (fetch_indeed, fetch_arbeitsagentur, fetch_target_companies, fetch_euraxess):
        try:
            all_jobs.extend(fetcher())
        except Exception as e:
            log.exception(f"{fetcher.__name__} failed: {e}")

    # Deduplicate within this run
    seen_in_run = set()
    new_jobs = []
    for j in all_jobs:
        jid = job_id(j["title"], j["company"], j["url"])
        if jid in seen or jid in seen_in_run:
            continue
        seen_in_run.add(jid)
        j["_id"] = jid
        new_jobs.append(j)

    log.info(f"New jobs after dedup: {len(new_jobs)}")

    new_jobs.sort(key=lambda x: x.get("posted", ""), reverse=True)
    new_jobs = new_jobs[:MAX_JOBS_IN_EMAIL]

    run_date = datetime.now(timezone.utc).strftime("%a %d %b %Y")
    subject = (
        f"🤖 {len(new_jobs)} new robotics jobs · {run_date}"
        if new_jobs else
        f"🤖 No new robotics jobs · {run_date}"
    )

    html = render_email(new_jobs, run_date)

    # Skip sending if zero AND env says so
    if not new_jobs and os.environ.get("SKIP_EMPTY_EMAIL", "false").lower() == "true":
        log.info("No new jobs and SKIP_EMPTY_EMAIL=true — exiting without email")
    else:
        try:
            send_email(subject, html)
        except KeyError as e:
            log.error(f"Missing SMTP env var: {e}. Email not sent.")
            # Still update state so a misconfigured email doesn't cause re-floods
        except Exception as e:
            log.exception(f"Email send failed: {e}")

    # Update state
    for j in new_jobs:
        seen.add(j["_id"])
    save_state(seen)
    log.info(f"State saved. Total seen: {len(seen)}")

    # Also write a JSON log of this run
    Path("last_run.json").write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "new_jobs_count": len(new_jobs),
        "jobs": new_jobs,
    }, indent=2, default=str))

if __name__ == "__main__":
    main()

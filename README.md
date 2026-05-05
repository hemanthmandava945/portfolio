# 🤖 Robotics Job Agent

Automated daily job digest for **robotics roles in Germany**, tailored to my profile (ROS 2, perception, VLA, sim-to-real, autonomous driving).

Runs on a free **GitHub Actions cron** every morning at **07:00 CET**, fetches jobs from multiple sources, filters them against my keywords, deduplicates against jobs I've already seen, and emails me an HTML digest.

---

## What it does

Each daily run:

1. Pulls jobs from:
   - **Indeed Germany** (RSS, per-keyword)
   - **Arbeitsagentur** (German federal employment agency public API)
   - **Greenhouse** boards of target companies (Agile Robots, NEURA, Wandelbots, Magazino, Franka, KUKA, Helsing, Wayve, Vay, Sereact)
   - **Lever** boards (Robco, ARX Robotics)
   - **EURAXESS** (EU research jobs RSS)
2. Filters titles + descriptions against an inclusion keyword list (robotics, ROS, perception, VLA, autonomous, …) and excludes irrelevant noise (RPA, sales, recruiter roles).
3. Deduplicates against `state.json` (jobs already mailed in past runs).
4. Sends an HTML email digest grouped by source.
5. Commits the updated `state.json` back to the repo so the next run remembers what you've seen.

---

## Setup (10 minutes, one-time)

### 1. Push this code to your repo

```bash
cd /path/to/this/folder
git init
git remote add origin git@github.com:hemanthmandava945/portfolio.git
git add .
git commit -m "Add robotics job agent"
git branch -M main
git push -u origin main
```

> **Tip:** if your `portfolio` repo already has content, put this agent in a subfolder
> like `job-agent/` or push it to a new dedicated repo (`robotics-job-agent`).
> The workflow path `.github/workflows/daily-jobs.yml` must be at the **root** of the repo.

### 2. Create a Gmail app password (or use any SMTP)

Easiest option: use Gmail with an **App Password** (not your real password).

1. Go to https://myaccount.google.com/security
2. Enable **2-Step Verification** if it isn't already.
3. Go to https://myaccount.google.com/apppasswords
4. Create a new app password called "Job Agent" — copy the 16-character password.

### 3. Add GitHub repository secrets

In your repo on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Add these five secrets:

| Name        | Value                                              |
|-------------|----------------------------------------------------|
| `SMTP_HOST` | `smtp.gmail.com`                                   |
| `SMTP_PORT` | `587`                                              |
| `SMTP_USER` | your Gmail address (e.g. `hemanthmandava1998@gmail.com`) |
| `SMTP_PASS` | the 16-character app password from step 2          |
| `TO_EMAIL`  | where you want the digest delivered (can be the same Gmail) |

### 4. Test it manually

In your repo on GitHub → **Actions** tab → **Daily Robotics Job Digest** → **Run workflow** (top right).

Watch the run, check your inbox. Done.

---

## Daily schedule

The workflow runs at `0 6 * * *` UTC = **07:00 Berlin time in winter, 08:00 in summer**.

Want a different time? Edit `.github/workflows/daily-jobs.yml` and change the cron line. Use https://crontab.guru/ to convert. Remember GitHub Actions uses UTC only.

Examples:
- `0 5 * * *` → 06:00 CET / 07:00 CEST
- `0 17 * * *` → 18:00 CET / 19:00 CEST (evening digest)
- `0 6 * * 1-5` → weekdays only

---

## Tweaking the filters

All lives in `scraper/agent.py` near the top:

- **`INCLUDE_KEYWORDS`** — at least one must appear in title or description for a job to be kept.
- **`EXCLUDE_KEYWORDS`** — drop jobs containing these (RPA, sales, etc.).
- **`GREENHOUSE_COMPANIES` / `LEVER_COMPANIES`** — slugs for company-specific scraping. Add more as you find them. The Greenhouse slug is the part of the URL after `boards.greenhouse.io/`.
- **`QUERIES`** — search terms used against Indeed and Arbeitsagentur.
- **`LOOKBACK_DAYS`** — how many days back to consider (default 2).

After editing, push to GitHub. The next scheduled run uses the new config.

---

## Files

```
.
├── .github/
│   └── workflows/
│       └── daily-jobs.yml       # Cron + CI/CD config
├── scraper/
│   └── agent.py                 # Main scraper / filter / mailer
├── requirements.txt             # Python dependencies
├── README.md                    # This file
├── state.json                   # AUTO-GENERATED: jobs already seen
└── last_run.json                # AUTO-GENERATED: log of last run output
```

---

## Costs & limits

- GitHub Actions free tier: **2,000 minutes/month** for private repos, **unlimited** for public repos.
- This workflow uses ~1 minute per run × 30 runs/month = ~30 minutes. Negligible.
- Gmail SMTP: 500 emails/day free.

---

## Troubleshooting

**"Workflow ran but I didn't get an email"**
→ Check the workflow logs in the Actions tab. Common causes: wrong app password, `SMTP_USER` doesn't match the account that generated the app password, or genuinely zero jobs matched (the email still sends with a "no jobs" message unless `SKIP_EMPTY_EMAIL=true`).

**"Same jobs keep showing up"**
→ The `state.json` commit step failed. Check the workflow log for the `Commit state` step. The `permissions: contents: write` block in the workflow file must be present.

**"Indeed RSS returned 0 results"**
→ Indeed sometimes rate-limits. The script just continues with the other sources. Not fatal.

**"Wrong jobs are matching"**
→ Add the noise keywords to `EXCLUDE_KEYWORDS` and push.

---

Built by **Hemanth Mandava** · Robotics Engineer · Reutlingen, DE

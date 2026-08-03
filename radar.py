"""Job radar — pull fresh SWE openings from company ATS boards into one shortlist.

There is no universal "all jobs" firehose: LinkedIn/Indeed block scraping. But
Greenhouse, Lever, and Ashby each expose a FREE public JSON board API. So the
model is: a curated company list (companies.txt) x its ATS API. For each target
company we fetch live postings, keep genuine software-engineering roles, tag the
entry-level ones, attach a posted date, and write a dated shortlist to JOBS.md.

Layout mirrors the top new-grad repos (SimplifyJobs style):
  - Entry-level / new-grad roles surfaced FIRST (that's what to apply to).
  - Every row shows Company | Role | Location | Posted (age) | Link.
  - Sorted newest-first; stale roles (> MAX_AGE_DAYS) dropped.

Runs locally (`python radar.py`) or in a GitHub Action on a daily cron.
No auth, no keys, no scraping — just three documented public endpoints.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

COMPANIES = "companies.txt"
OUT = "JOBS.md"
MAX_AGE_DAYS = 60          # drop anything older than this (stale)
NOW = datetime.now(timezone.utc)

# ---- what counts as a software-engineering role (precise, not "any engineer") ----
ROLE = re.compile(
    r"\b(software\s+engineer|software\s+developer|"
    r"software\s+development\s+engineer|back[\s-]?end\s+engineer|"
    r"front[\s-]?end\s+engineer|full[\s-]?stack\s+(engineer|developer)|"
    r"web\s+developer|application\s+developer|platform\s+engineer|"
    r"infrastructure\s+engineer|programmer|swe|sde)\b", re.I)
# Drop senior/managerial/specialist noise even if it looks like a SWE role.
DROP = re.compile(
    r"(\b(senior|staff|principal|lead|manager|director|head\s+of|vp|"
    r"solutions\s+(engineer|architect)|forward\s+deployed|sales|marketing|"
    r"recruit|II|III|IV|L[4-9])\b|\bsr\b)", re.I)

# ---- entry-level tiers (be specific, like the user asked) ----
NEWGRAD = re.compile(
    r"\b(new\s?grad|graduate|university\s?grad|campus|early\s?career|"
    r"entry[\s-]?level|university)\b", re.I)
JUNIOR = re.compile(
    r"\b(junior|jr\.?|associate\s+(software|engineer)|"
    r"software\s+engineer\s+i\b|engineer\s+i\b|sde\s?(1|i)\b|"
    r"level\s?1|entry)\b", re.I)

# ---- location: US / US-remote only ----
NON_US = re.compile(
    r"\b(canada|poland|spain|india|brazil|germany|uk|ireland|mexico|"
    r"portugal|netherlands|france|australia|singapore|japan|israel|"
    r"emea|apac|latam|europe)\b", re.I)
US_OK = re.compile(r"\b(united states|usa|u\.s\.|remote|us[\s-]|, [A-Z]{2}\b|"
                   r"new york|san francisco|seattle|austin|boston|chicago|"
                   r"tempe|phoenix|arizona|texas|california|washington)\b", re.I)


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "job-radar/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def parse_dt(v):
    """ATS dates come as ISO strings (Greenhouse/Ashby) or epoch ms (Lever)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def tier(title: str) -> str | None:
    """'newgrad' / 'entry' for junior roles, None for general (non-senior) SWE."""
    if NEWGRAD.search(title):
        return "newgrad"
    if JUNIOR.search(title):
        return "entry"
    return None


def is_swe(title: str) -> bool:
    return bool(ROLE.search(title)) and not DROP.search(title)


def in_us(loc: str) -> bool:
    if NON_US.search(loc or ""):
        return False
    return US_OK.search(loc or "") is not None or loc == ""


# ---- one fetcher per ATS: each returns [{title, loc, url, posted}] ----
def greenhouse(slug: str):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false")
    return [{"title": j["title"], "loc": (j.get("location") or {}).get("name", ""),
             "url": j["absolute_url"], "posted": parse_dt(j.get("updated_at"))}
            for j in d.get("jobs", [])]


def lever(slug: str):
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [{"title": j["text"],
             "loc": (j.get("categories") or {}).get("location", ""),
             "url": j["hostedUrl"], "posted": parse_dt(j.get("createdAt"))}
            for j in d]


def ashby(slug: str):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [{"title": j["title"], "loc": j.get("location", ""),
             "url": j["jobUrl"],
             "posted": parse_dt(j.get("publishedAt") or j.get("updatedAt"))}
            for j in d.get("jobs", [])]


FETCH = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def load_companies():
    out = []
    with open(COMPANIES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else parts[1]))
    return out


def age_days(posted):
    return None if posted is None else (NOW - posted).days


def age_str(days):
    if days is None:
        return "—"
    if days <= 0:
        return "today"
    return f"{days}d ago"


def table(rows):
    """rows: [(company, title, loc, days, url)] already sorted."""
    out = ["| Company | Role | Location | Posted | Link |", "|---|---|---|---|---|"]
    for co, title, loc, days, url in rows:
        loc = (loc or "").replace("|", "/")
        out.append(f"| {co} | {title} | {loc} | {age_str(days)} | [apply]({url}) |")
    return "\n".join(out)


def main():
    entry_rows, other_rows, failed, ok = [], [], [], 0
    for ats, slug, name in load_companies():
        fetch = FETCH.get(ats)
        if not fetch:
            failed.append(f"{name} (unknown ats '{ats}')")
            continue
        try:
            jobs = fetch(slug)
        except urllib.error.HTTPError as e:
            failed.append(f"{name} (HTTP {e.code} — check slug '{slug}')")
            continue
        except Exception as e:  # noqa: BLE001 - one bad board shouldn't kill the run
            failed.append(f"{name} ({type(e).__name__})")
            continue
        ok += 1
        kept = 0
        for j in jobs:
            if not is_swe(j["title"]) or not in_us(j["loc"]):
                continue
            days = age_days(j["posted"])
            if days is not None and days > MAX_AGE_DAYS:
                continue
            row = (name, j["title"].strip(), j["loc"], days, j["url"])
            (entry_rows if tier(j["title"]) else other_rows).append(row)
            kept += 1
        print(f"  {name:14} {kept:3} kept / {len(jobs)} total")

    # newest first; unknown dates sink to the bottom
    def by_age(r):
        return (r[3] is None, r[3] if r[3] is not None else 10**9)
    entry_rows.sort(key=by_age)
    other_rows.sort(key=by_age)

    total = len(entry_rows) + len(other_rows)
    lines = [
        f"# Job Radar — {total} roles you can apply to",
        f"\n_Auto-pulled from Greenhouse / Lever / Ashby public APIs across {ok} "
        f"sponsor-friendly companies. Non-senior US software-engineering roles, "
        f"newest first; older than {MAX_AGE_DAYS} days dropped. "
        f"Edit `companies.txt` to add companies._\n",
        f"## 🎓 Explicitly Entry-Level / New Grad ({len(entry_rows)})\n",
        "_Titles that literally say New Grad / Entry / Junior / Engineer I. "
        "These are seasonal (peak Aug–Oct), so this list is often short._\n",
        table(entry_rows) if entry_rows else "_None titled that way right now._",
        f"\n## 💻 Software Engineer — apply to these too ({len(other_rows)})\n",
        "_Non-senior SWE roles with no level in the title. A new grad applies "
        "to these — this is your main pile._\n",
        table(other_rows) if other_rows else "_None._",
    ]
    if failed:
        lines += ["\n<details><summary>Boards that failed (fix the slug in "
                  "companies.txt)</summary>\n"]
        lines += [f"- {f}" for f in failed]
        lines += ["\n</details>"]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {OUT}: {len(entry_rows)} entry + {len(other_rows)} other, "
          f"{ok} boards ok, {len(failed)} failed")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

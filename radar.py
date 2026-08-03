"""Job radar — pull fresh openings from company ATS boards into one Markdown file.

There is no universal "all jobs" firehose: LinkedIn/Indeed block scraping. But
Greenhouse, Lever, and Ashby each expose a FREE public JSON board API. So the
model here is: a curated company list (companies.txt) x its ATS API. For each
target company we fetch live postings, keep the ones that look like roles I'd
apply to, and write a dated shortlist to JOBS.md.

Runs locally (`python radar.py`) or in a GitHub Action on a daily cron.
No auth, no keys, no scraping — just three documented public endpoints.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

COMPANIES = "companies.txt"
OUT = "JOBS.md"

# A role is "relevant" if its title matches these — tuned for new-grad backend/
# full-stack SWE. Widen or narrow to taste.
KEEP = re.compile(
    r"\b(software|backend|back-end|full[\s-]?stack|platform|infrastructure|"
    r"distributed|api|new\s?grad|graduate|early\s?career|associate|"
    r"junior|entry|swe|developer|engineer)\b", re.I)
# Drop senior/managerial/non-eng noise even if "engineer" appears.
DROP = re.compile(
    r"(\b(senior|staff|principal|lead|manager|director|head\s+of|vp|"
    r"machine\s?learning|ml|data\s?scien|security|sales|marketing|"
    r"business\s+development|solutions\s+(engineer|architect)|partner\s+engineer|"
    r"forward\s+deployed|service\s+desk|analytics|recruit|designer|"
    r"II|III|IV|L[4-9])\b|\bsr\b)", re.I)
# Keep US / US-remote only. First reject anything naming a foreign location
# (so "Remote Poland" / "Remote - India" get dropped), then require a US signal.
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


def relevant(title: str, loc: str) -> bool:
    if not KEEP.search(title) or DROP.search(title):
        return False
    if NON_US.search(loc or ""):
        return False
    return US_OK.search(loc or "") is not None or loc == ""


# ---- one fetcher per ATS: each returns a list of {title, loc, url} ----
def greenhouse(slug: str):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false")
    return [{"title": j["title"], "loc": (j.get("location") or {}).get("name", ""),
             "url": j["absolute_url"]} for j in d.get("jobs", [])]


def lever(slug: str):
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [{"title": j["text"],
             "loc": (j.get("categories") or {}).get("location", ""),
             "url": j["hostedUrl"]} for j in d]


def ashby(slug: str):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [{"title": j["title"], "loc": j.get("location", ""),
             "url": j["jobUrl"]} for j in d.get("jobs", [])]


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
            ats, slug = parts[0], parts[1]
            name = parts[2] if len(parts) > 2 else slug
            out.append((ats, slug, name))
    return out


def main():
    companies = load_companies()
    rows, ok, failed = [], 0, []
    for ats, slug, name in companies:
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
        hits = [j for j in jobs if relevant(j["title"], j["loc"])]
        for j in hits:
            rows.append((name, j["title"], j["loc"], j["url"]))
        print(f"  {name:14} {len(hits):3} relevant / {len(jobs)} total")

    rows.sort(key=lambda r: (r[0], r[1]))
    lines = [f"# Job Radar — {len(rows)} matching roles across {ok} companies\n",
             "_Auto-pulled from Greenhouse / Lever / Ashby public APIs. "
             "Edit `companies.txt` to change targets._\n",
             "| Company | Role | Location | Link |",
             "|---|---|---|---|"]
    for name, title, loc, url in rows:
        loc = (loc or "").replace("|", "/")
        lines.append(f"| {name} | {title} | {loc} | [apply]({url}) |")
    if failed:
        lines += ["", "<details><summary>Boards that failed (fix the slug in "
                  "companies.txt)</summary>", ""]
        lines += [f"- {f}" for f in failed]
        lines += ["</details>"]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {OUT}: {len(rows)} roles, {ok} boards ok, {len(failed)} failed")
    if not rows and ok == 0:
        sys.exit(1)  # every board failed — surface it in CI


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Audit FRESH.md row by row, from scratch, and print the evidence for every claim.

fresh.py asserts three things about each row. This re-derives all three from the
original sources, without reusing any of fresh.py's own results, so a disagreement
between the two is visible rather than silent:

  1. first published within the window  -- re-fetched from the employer's API, and
     shown next to updated_at so a repost is obvious
  2. still open right now               -- the posting must still be on the live board
  3. no application in the mail window  -- the mailbox is searched again per company

Usage:  python3 verify.py [--days 7] [--applied-window 15]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import fresh as F
import radar as R

FRESH = Path(__file__).with_name("FRESH.md")


def rows_from_report() -> list[dict[str, str]]:
    """Parse the FRESH.md table.

    Current layout (6 columns, tier first):
        | Tier | Company | Role | Location | First posted | Apply |
    The pre-tier layout had 7 columns with company first; accept both so an old
    report still audits rather than silently parsing to zero rows.
    """
    out = []
    for line in FRESH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith(
            ("| Tier", "| Company", "|---")
        ):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) == 6:  # tier-first layout
            company, role, posted, link_cell = c[1], c[2], c[4], c[5]
        elif len(c) == 7:  # legacy company-first layout
            company, role, posted, link_cell = c[0], c[1], c[4], c[6]
        else:
            continue
        m = re.search(r"\((http[^)]+)\)", link_cell)
        if m:
            out.append({"company": company.strip("* "), "role": role,
                        "posted": posted, "url": m.group(1)})
    return out


def evidence(row: dict[str, str]) -> dict[str, Any]:
    """Go back to the employer's API for this one posting."""
    url: str = row["url"]
    out: dict[str, Any] = {}
    # Greenhouse: slug + numeric id, from either the board URL or a gh_jid parameter.
    gh = re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url)
    jid = re.search(r"gh_jid=(\d+)", url)
    if gh or jid:
        slug = gh.group(1) if gh else None
        job_id = gh.group(2) if gh else jid.group(1)
        if slug is None:
            # careerpuck-style wrapper: app.careerpuck.com/job-board/<slug>/job/<id>
            if wrap := re.search(r"/job-board/([\w-]+)/", url):
                slug = wrap.group(1)
        for candidate in filter(None, [slug, R.norm(row["company"]).replace(" ", "")]):
            try:
                d = R.get("https://boards-api.greenhouse.io/v1/boards/"
                          f"{candidate}/jobs/{job_id}")
                out["first_published"] = d.get("first_published")
                out["updated_at"] = d.get("updated_at")
                out["title_on_board"] = d.get("title")
                out["live"] = True
                return out
            except (OSError, ValueError, KeyError):
                continue
        out["live"] = None  # could not resolve the board slug from the URL
        return out

    if ash := re.search(r"ashbyhq\.com/([\w.\-]+)/([0-9a-f-]{36})", url):
        slug, job_id = ash.group(1), ash.group(2)
        try:
            d = R.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            for j in d.get("jobs", []):
                if j.get("id") == job_id:
                    out["first_published"] = j.get("publishedAt")
                    out["updated_at"] = j.get("updatedAt")
                    out["title_on_board"] = j.get("title")
                    out["live"] = j.get("isListed") is not False
                    return out
            out["live"] = False  # id no longer on the board = closed
        except (OSError, ValueError, KeyError):
            out["live"] = None
        return out

    # SmartRecruiters: releasedDate is the publish stamp, `active` is liveness.
    if sr := re.search(r"smartrecruiters\.com/([\w-]+)/(\d+)", url):
        company_slug, job_id = sr.groups()
        try:
            d = R.get("https://api.smartrecruiters.com/v1/companies/"
                      f"{company_slug}/postings/{job_id}")
            out["first_published"] = d.get("releasedDate")
            out["updated_at"] = d.get("lastUpdatedOn")
            out["title_on_board"] = d.get("name")
            out["live"] = bool(d.get("active"))
        except (OSError, ValueError, KeyError):
            out["live"] = None
        return out

    # Workday: /en-US/<site>/job/<loc>/<slug>_<req> -> re-derive from the detail endpoint,
    # which is the only place an exact ISO date lives.
    if wd := re.search(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/en-US/([^/]+)(/job/.+)$",
                       url):
        tenant, wdn, site, path = wd.groups()
        try:
            d = R.get(f"https://{tenant}.{wdn}.myworkdayjobs.com/wday/cxs/"
                      f"{tenant}/{site}{path}")
            info = d.get("jobPostingInfo", {})
            out["first_published"] = info.get("startDate")
            out["updated_at"] = None          # Workday exposes no edit stamp
            out["title_on_board"] = info.get("title")
            out["req_id"] = info.get("jobReqId")
            out["live"] = not info.get("jobPostingIsExpired", False)
        except (OSError, ValueError, KeyError):
            out["live"] = None
        return out

    if lev := re.search(r"lever\.co/([\w-]+)/([0-9a-f-]{36})", url):
        slug, job_id = lev.group(1), lev.group(2)
        try:
            for j in R.get(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
                if j.get("id") == job_id:
                    out["first_published"] = R.parse_dt(j.get("createdAt"))
                    out["updated_at"] = None  # Lever exposes no separate edit stamp
                    out["title_on_board"] = j.get("text")
                    out["live"] = True
                    return out
            out["live"] = False
        except (OSError, ValueError, KeyError):
            out["live"] = None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--applied-window", type=int, default=15)
    args = ap.parse_args()

    rows = rows_from_report()
    print(f"auditing {len(rows)} rows in {FRESH.name}\n")

    print(f"searching the mailbox for confirmations in the last {args.applied_window} days")
    pairs = F.applied_recently(args.applied_window)
    print(f"  {len(pairs)} confirmations found\n")

    bad = 0
    for r in rows:
        ev = evidence(r)
        fp = ev.get("first_published")
        fp_dt = fp if hasattr(fp, "year") else R.parse_dt(fp)
        age = (F.NOW - fp_dt).days if fp_dt else None
        up = ev.get("updated_at")
        up_dt = up if hasattr(up, "year") else R.parse_dt(up)
        hit = F.already_applied(r["company"].split(" (applied")[0], pairs)

        problems = []
        if ev.get("live") is False:
            problems.append("NOT LIVE")
        if age is None:
            problems.append("no first-publish date")
        elif age > args.days:
            problems.append(f"OLDER THAN {args.days}d ({age}d)")
        if hit:
            problems.append(f"APPLIED: {hit[:60]}")
        bad += bool(problems)

        flag = "FAIL" if problems else "ok  "
        print(f"{flag} {r['company'][:26]:28} {r['role'][:38]:40}")
        print(f"       first published {str(fp_dt)[:19] or '?':20} -> {age}d old"
              f"   (report said {r['posted']})")
        if up_dt and fp_dt and (up_dt - fp_dt).days > 1:
            print(f"       updated_at      {str(up_dt)[:19]:20} -> "
                  f"{(F.NOW - up_dt).days}d  <-- edited since; a tool reading this "
                  f"field would call it {(F.NOW - up_dt).days}d old")
        req = f"  req {ev['req_id']}" if ev.get("req_id") else ""
        print(f"       live on board   {ev.get('live')}{req}    mail match: "
              f"{hit[:48] if hit else 'none'}")
        if problems:
            print(f"       PROBLEMS: {', '.join(problems)}")
        print()

    print(f"{len(rows) - bad}/{len(rows)} rows verified against the original sources")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

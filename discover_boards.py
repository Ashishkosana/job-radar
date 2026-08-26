#!/usr/bin/env python3
"""discover_boards.py — grow companies.txt from the community new-grad feeds.

The freshness gate in `fresh.py` can only see companies whose board we poll, so the
binding constraint on "what is new today" is the length of companies.txt, not the
filter. The community listings.json feeds already carry direct apply URLs for
hundreds of employers, and an apply URL contains the ATS slug:

    job-boards.greenhouse.io/<slug>/jobs/<id>
    jobs.lever.co/<slug>/<uuid>
    jobs.ashbyhq.com/<slug>/<uuid>
    jobs.smartrecruiters.com/<slug>/<id>

So: harvest every slug, drop the ones already listed, verify each board answers its
ATS API with at least one posting, and append the survivors. Verification matters
because a slug lifted from a stale listing is often a board that no longer exists,
and an unverified line just makes radar.py noisier.

    python3 discover_boards.py            # dry run, prints what it would add
    python3 discover_boards.py --write    # append verified boards to companies.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import radar as R

COMPANIES = Path(__file__).with_name("companies.txt")

# (ats, regex over the apply URL). First capture group is the slug.
PATTERNS = (
    ("greenhouse", re.compile(r"(?:job-)?boards?\.greenhouse\.io/(?:embed/job_app\?for=)?"
                              r"([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)", re.I)),
)
PROBES = {
    "greenhouse": lambda s: R.get(
        f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs").get("jobs"),
    "lever": lambda s: R.get(f"https://api.lever.co/v0/postings/{s}?mode=json"),
    "ashby": lambda s: R.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{s}").get("jobs"),
    "smartrecruiters": lambda s: R.get(
        f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1").get("content"),
}


def harvest() -> dict[tuple[str, str], str]:
    """{(ats, slug): display name} from every community feed."""
    found: dict[tuple[str, str], str] = {}
    for name, url in R.LISTINGS_JSON.items():
        try:
            rows = R.get(url)
        except (OSError, json.JSONDecodeError):
            print(f"  {name}: unreachable", file=sys.stderr)
            continue
        hits = 0
        for row in rows if isinstance(rows, list) else []:
            link = row.get("url") or row.get("apply_link") or ""
            company = (row.get("company_name") or row.get("company") or "").strip()
            for ats, pat in PATTERNS:
                m = pat.search(link)
                if m:
                    slug = m.group(1).lower()
                    if slug in {"jobs", "embed", "www"}:
                        continue
                    found.setdefault((ats, slug), company or slug)
                    hits += 1
                    break
        print(f"  {name}: {hits} slugs")
    return found


def verify(item: tuple[tuple[str, str], str]) -> tuple[tuple[str, str], str] | None:
    (ats, slug), name = item
    try:
        postings = PROBES[ats](slug)
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        # OSError covers URLError, HTTPError and socket.timeout. A probe that cannot
        # answer is simply not added; this loop must never abort the whole run.
        return None
    return item if postings else None  # a board with zero postings is not worth polling


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="append to companies.txt")
    args = ap.parse_args()

    existing_lines = COMPANIES.read_text(encoding="utf-8").splitlines()
    have = {(p[0], p[1].lower()) for line in existing_lines
            if (p := line.split()) and not line.startswith("#") and len(p) >= 2}
    print(f"companies.txt has {len(have)} boards")

    print("harvesting community feeds:")
    candidates = {k: v for k, v in harvest().items() if k not in have}
    print(f"{len(candidates)} new candidate boards; verifying against the ATS APIs...")

    with ThreadPoolExecutor(max_workers=16) as pool:
        verified = [r for r in pool.map(verify, candidates.items()) if r]
    verified.sort(key=lambda r: (r[0][0], r[0][1]))
    print(f"{len(verified)} verified live with at least one posting")

    lines = [f"{ats:<16}{slug:<28}{name}" for (ats, slug), name in verified]
    if not args.write:
        print("\n".join(lines[:40]))
        if len(lines) > 40:
            print(f"... and {len(lines) - 40} more")
        print("\n(dry run — pass --write to append)")
        return 0

    with COMPANIES.open("a", encoding="utf-8") as f:
        f.write("\n# --- discovered from community feeds, verified live ---\n")
        f.write("\n".join(lines) + "\n")
    print(f"appended {len(lines)} boards to {COMPANIES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

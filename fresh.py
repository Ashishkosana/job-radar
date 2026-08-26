#!/usr/bin/env python3
"""fresh.py — the strict shortlist: posted in the last N days, live now, not already applied.

`radar.py` casts a wide net (45-day window, aggregator feeds included). This is the
opposite: a small list you can act on the same morning. Three gates radar does not apply.

1. FIRST published, not "updated".
   A Greenhouse repost bumps `updated_at` and hides an old req as a fresh one. Verisign's
   Junior SWE req reads `updated_at = 2026-08-06` (4 days old) and
   `first_published = 2026-07-01` (40 days old). Same trap in reverse on aggregators, whose
   "reposted 2 days ago" is the aggregator's own crawl date, not the employer's. So this
   reads only the employer's own first-publish field, and drops a row when there isn't one.

2. Live right now.
   Every row here came out of the employer's own ATS feed during this run, so presence is
   proof of listing. Aggregators (community listing repos, The Muse) are excluded entirely:
   their dates lag and a closed req can sit in their JSON for weeks.

3. Not already applied.
   One Gmail pass over the last APPLIED_WINDOW_DAYS of application confirmations, matched
   against each company. Reuses the Gmail MCP's OAuth token, so there is nothing to set up.
   Confirmations arrive from the ATS (`salesforce@myworkday.com`, `no-reply@torc.ai`), NOT
   from a careers@company address, which is why the sender list below is ATS-first.

Usage:
    python3 fresh.py                 # writes FRESH.md
    python3 fresh.py --days 7        # widen the freshness window
    python3 fresh.py --no-gmail      # skip the applied check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import archetype
import eligibility
import radar as R

FRESH_DAYS = 3
APPLIED_WINDOW_DAYS = 15
OUT = Path(__file__).with_name("FRESH.md")
APPLIED = Path(__file__).with_name("applied.txt")
GMAIL_DIR = Path.home() / ".gmail-mcp"
NOW = datetime.now(timezone.utc)

# Confirmations come from the ATS, not the company. Missing these is how a duplicate
# application slips through: a `from:salesforce.com` search never sees myworkday.com.
ATS_SENDERS = (
    "myworkday.com", "myworkdaysite.com", "greenhouse-mail.io", "ashbyhq.com",
    "hire.lever.co", "lever.co", "bamboohr.com", "icims.com", "successfactors.com",
    "smartrecruiters.com", "workable.com", "jobvite.com", "taleo.net", "brassring.com",
    "oraclecloud.com", "avature.net", "paylocity.com", "dayforcehcm.com", "ripplingats.com",
)
APPLIED_SUBJECTS = ("applying", "application", "applied", "we received", "received your")
# He is looking for full-time work; radar's DROP list does not cover intern reqs.
INTERNSHIP = re.compile(r"\b(intern|internship|co[\s-]?op|summer\s+20\d\d)\b", re.I)
# Cleared work he cannot hold on F-1, which the title/location filter misses because the
# requirement lives in the JD body. Two cheap high-precision signals instead:
# the employer, and the facility. Annapolis Junction alone produced 40 rows in one run.
DEFENSE_EMPLOYERS = {
    "captivation", "captivation software", "anduril", "anduril industries", "spacex",
    "varda", "varda space", "varda space industries", "defense unicorns",
    "clarity innovations", "voyager technologies", "babel street", "two six technologies",
    "shield ai", "epirus", "applied intuition", "govini", "primer", "vannevar labs",
    "second front", "rebellion defense", "scale ai federal", "palantir federal",
}
# radar's in_us() answers True for "United Kingdom (Remote)" because US_OK matches the
# bare word "Remote". A non-US country name has to win over that.
NON_US_STRICT = re.compile(
    r"\b(united kingdom|england|scotland|ireland|dublin|london|canada|toronto|vancouver|"
    r"india|bengaluru|bangalore|hyderabad|germany|berlin|munich|france|paris|spain|"
    r"portugal|lisbon|poland|warsaw|netherlands|amsterdam|sweden|denmark|norway|"
    r"singapore|australia|sydney|japan|tokyo|israel|tel aviv|brazil|mexico|emea|apac)\b",
    re.I)
CLEARED_SITES = re.compile(
    r"(annapolis junction|fort meade|ft\.? meade|\bafb\b|air force base|quantico|"
    r"langley|redstone|aberdeen proving|wright[\s-]patterson|patuxent|"
    r"scott air force|peterson space|schriever|hanscom|eglin|tinker|robins)", re.I)
# Requirements live in the BODY, not the title. Across one vetting pass, 20 of 27
# titles that read "Software Engineer" turned out to be 3-to-10-year reqs, ITAR roles or
# clearance roles -- so these gates read the JD text before a role reaches the list.
YEARS = re.compile(r"(\d{1,2})\s*(?:\+|to|-|\u2013)?\s*(?:\d{1,2})?\s*\+?\s*years?\b", re.I)
INELIGIBLE = re.compile(
    r"(itar\b|international traffic in arms|\bu\.?s\.? person\b|export control|"
    r"security clearance|\btop[\s-]?secret\b|\bts/sci\b|\bsci\b clearance|active secret|"
    r"must be a u\.?s\.? citizen|u\.?s\.? citizenship (?:is )?required)", re.I)
# An explicit OPT exclusion is different from "we don't sponsor": the first bars him
# outright, the second is a runway play he may still choose.
ANTI_OPT = re.compile(
    r"(not\s+(?:currently\s+)?(?:be\s+)?able to (?:engage|hire|consider)[^.]{0,40}\bOPT\b|"
    r"\bno\b[^.]{0,20}\bOPT\b|cannot[^.]{0,30}\bOPT\b|"
    # "will not ... participate in STEM OPT" bars him outright and is NOT the same as a
    # we-do-not-sponsor statement. PNC wrote exactly that and the first pattern missed it.
    r"will not[^.]{0,80}\bOPT\b|"
    r"(?:not|ineligible)[^.]{0,40}participat\w*[^.]{0,40}\bOPT\b|"
    r"\bOPT\b[^.]{0,30}(?:are|is) not (?:eligible|accepted|considered))", re.I)

# Stripped before matching so "Asure Software" and "Asure" compare equal.
CORP_SUFFIXES = (
    " incorporated", " inc", " llc", " ltd", " limited", " corporation", " corp", " company",
    " co", " technologies", " technology", " software", " systems", " solutions", " labs",
    " group", " holdings", " ai", " io", " hq",
)


# ---------------------------------------------------------------- freshness-aware sources


def _row(company: str, title: str, loc: str, url: str,
         published: datetime | None, source: str, slug: str = "",
         jid: str = "") -> dict[str, Any]:
    return {"company": company, "title": title, "loc": loc, "url": url,
            "published": published, "source": source, "slug": slug, "jid": str(jid)}


def greenhouse(slug: str, name: str) -> list[dict[str, Any]]:
    d = R.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false")
    return [_row(name, j["title"], (j.get("location") or {}).get("name", ""),
                 j["absolute_url"],
                 # first_published only. updated_at is the repost field.
                 R.parse_dt(j.get("first_published")), "greenhouse", slug, j["id"])
            for j in d.get("jobs", [])]


def lever(slug: str, name: str) -> list[dict[str, Any]]:
    d = R.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [_row(name, j["text"], (j.get("categories") or {}).get("location", ""),
                 j["hostedUrl"], R.parse_dt(j.get("createdAt")), "lever",
                 slug, j.get("id", "")) for j in d]


def ashby(slug: str, name: str) -> list[dict[str, Any]]:
    d = R.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    rows = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:  # explicit unlisted: not live
            continue
        # publishedAt only; radar falls back to updatedAt, which hides reposts.
        rows.append(_row(name, j["title"], j.get("location", ""), j["jobUrl"],
                         R.parse_dt(j.get("publishedAt")), "ashby", slug, j.get("id", "")))
    return rows


def smartrecruiters(slug: str, name: str) -> list[dict[str, Any]]:
    d = R.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
              f"?country=us&limit=100")
    return [_row(name, j["name"], ((j.get("location") or {}).get("city") or ""),
                 f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                 R.parse_dt(j.get("releasedDate")), "smartrecruiters")
            for j in d.get("content", [])]


def workday(spec: str, name: str) -> list[dict[str, Any]]:
    """Workday CXS search. spec is "<tenant>/<wdN>/<site>".

    Workday states freshness as human text ("Posted Today", "Posted 3 Days Ago"), which
    is exactly precise enough for a short window -- only the bucketed "Posted 30+ Days
    Ago" is vague, and those fall outside the window anyway. Excluding Workday for
    lacking an ISO date was a mistake: it removed every bank, insurer and large retailer,
    which is also the most sponsorship-friendly slice of the market.
    """
    tenant, wd, site = spec.split("/")
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    rows: list[dict[str, Any]] = []
    for term in ("software engineer", "software developer"):
        for offset in (0, 20):
            body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": offset,
                               "searchText": term}).encode()
            d = R.get(url, data=body, headers={"Content-Type": "application/json"})
            for j in d.get("jobPostings", []):
                path = j.get("externalPath", "")
                rows.append(_row(
                    name, j.get("title", ""), j.get("locationsText", ""),
                    f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}",
                    R.workday_posted(j.get("postedOn")), "workday", spec, path))
            if len(d.get("jobPostings", [])) < 20:
                break
    return rows


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters, "workday": workday}


# ------------------------------------------------------------------------------- gmail


def gmail_token() -> str:
    """Mint an access token from the Gmail MCP's stored refresh token."""
    creds = json.loads((GMAIL_DIR / "credentials.json").read_text())
    keys = json.loads((GMAIL_DIR / "gcp-oauth.keys.json").read_text())["installed"]
    body = urllib.parse.urlencode({
        "client_id": keys["client_id"],
        "client_secret": keys["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    d = R.get(keys["token_uri"], data=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    return str(d["access_token"])


def _get_retry(url: str, headers: dict[str, str], tries: int = 4) -> Any:
    """Gmail metadata fetch with backoff.

    The mail pass makes one request per confirmation (hundreds), and a single
    ConnectionResetError used to abort the whole check -- which fresh.py then treated as
    "no dedup available" and let duplicates through. Retry, and let the caller count
    anything that still fails so coverage is reported rather than assumed.
    """
    for attempt in range(tries):
        try:
            return R.get(url, headers=headers)
        except (OSError, json.JSONDecodeError):
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return None


def applied_recently(window_days: int) -> list[tuple[str, str]]:
    """Every application confirmation in the window, as (from, subject) pairs."""
    token = gmail_token()
    auth = {"Authorization": f"Bearer {token}"}
    senders = " OR ".join(ATS_SENDERS)
    subjects = " OR ".join(f'"{s}"' for s in APPLIED_SUBJECTS)
    q = f"newer_than:{window_days}d (from:({senders}) OR subject:({subjects}))"
    base = ("https://gmail.googleapis.com/gmail/v1/users/me/messages"
            f"?maxResults=500&q={urllib.parse.quote(q)}")

    # Page to the end. A truncated listing is the dangerous failure here: it yields a
    # false "not applied" and a duplicate submission, which is the one thing this gate exists
    # to prevent.
    ids: list[str] = []
    page: str | None = None
    while True:
        listing = R.get(base + (f"&pageToken={page}" if page else ""), headers=auth)
        ids.extend(m["id"] for m in listing.get("messages", []))
        page = listing.get("nextPageToken")
        if not page:
            break

    pairs: list[tuple[str, str]] = []
    unread = 0
    for mid in ids:
        try:
            meta = _get_retry(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}"
                "?format=metadata&metadataHeaders=From&metadataHeaders=Subject", auth)
        except (OSError, json.JSONDecodeError):
            unread += 1
            continue
        h = {x["name"].lower(): x["value"]
             for x in meta.get("payload", {}).get("headers", [])}
        pairs.append((h.get("from", ""), h.get("subject", "")))
    if unread:
        # Say it out loud: an unread confirmation is a duplicate this run cannot catch.
        print(f"  WARNING: {unread} of {len(ids)} confirmations could not be read; "
              "the duplicate check is incomplete for this run", file=sys.stderr)
    return pairs


def body_of(row: dict[str, Any]) -> str:
    """The posting's text. One fetch per survivor, not per posting scanned."""
    try:
        if row["source"] == "greenhouse":
            return R.get("https://boards-api.greenhouse.io/v1/boards/"
                         f"{row['slug']}/jobs/{row['jid']}").get("content", "")
        if row["source"] == "lever":
            d = R.get(f"https://api.lever.co/v0/postings/{row['slug']}?mode=json")
            for j in d:
                if str(j.get("id")) == row["jid"]:
                    return j.get("descriptionPlain", "") + " " + str(j.get("lists", ""))
        if row["source"] == "workday":
            tenant, wd, site = row["slug"].split("/")
            d = R.get(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/"
                      f"{tenant}/{site}{row['jid']}")
            info = d.get("jobPostingInfo", {})
            # postedOn is a relative bucket ("Posted 30+ Days Ago"); startDate is exact.
            # Overwrite the estimate so the freshness re-check below judges the real date.
            if exact := R.parse_dt(info.get("startDate")):
                row["published"] = exact
            # Trust Workday's own externalUrl over anything assembled by hand: inserting
            # an /en-US/ locale segment produced links that loaded a shell page with no job.
            if ext := info.get("externalUrl"):
                row["url"] = ext
            return str(info.get("jobDescription", ""))
        if row["source"] == "ashby":
            d = R.get(f"https://api.ashbyhq.com/posting-api/job-board/{row['slug']}")
            for j in d.get("jobs", []):
                if str(j.get("id")) == row["jid"]:
                    return j.get("descriptionPlain", "")
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return ""
    return ""


# Requirements are often written in words, not digits: a role titled "Software Engineer 1"
# at a bank asked for "Two to three years of relevant experience" and sailed through a
# digits-only gate.
WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}
WORD_YEARS = re.compile(
    r"\b(" + "|".join(WORD_NUM) + r")\b(?:\s*(?:to|-|or more|\+)?\s*"
    r"(?:" + "|".join(WORD_NUM) + r")?\s*)?\s*years?\b", re.I)


def years_floor(body: str) -> int | None:
    """Lowest number of years the posting asks for, or None if it never says.

    Takes the minimum across all mentions on purpose: a JD saying "3+ years backend,
    1+ year Python" is really asking for 1, and erring toward keeping is safer than
    silently hiding an entry-level req.
    """
    def is_experience(m: "re.Match[str]") -> bool:
        """Reject year mentions that are not an experience requirement.

        Three real false positives from a corpus of 83 job descriptions:
        company-age boilerplate ("For over 20 years, Acme has been..."), the legal
        minimum ("18+ years of age"), and narrative prose. Requiring an
        experience-ish word nearby, and rejecting age and company-history context,
        removes all three without losing real requirement lines.
        """
        window = body[max(0, m.start() - 90): m.end() + 90].lower()
        if re.search(r"years? of age|\bage\b|for (?:over|more than) \d+ years|"
                     r"(?:founded|since|established)\b|\d+ years,\s|"
                     r"years? (?:stale|old|ago)", window):
            return False
        return bool(re.search(
            r"experience|\bexp\b|background|working|professional|industry|"
            r"building|developing|hands.on|track record|minimum|qualification", window))

    found = [int(m.group(1)) for m in YEARS.finditer(body)
             if int(m.group(1)) <= 20 and is_experience(m)]
    found += [WORD_NUM[m.group(1).lower()] for m in WORD_YEARS.finditer(body)
              if is_experience(m)]
    return min(found) if found else None


def squash(name: str) -> str:
    """'Asure Software, Inc.' -> 'asure'  (so it compares equal to 'Asure')."""
    # Collapse runs of whitespace first: "Asure Software,  Inc." would otherwise end in a
    # space, endswith(" software") would miss, and the suffix peel would stop one short.
    n = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", name.lower())).strip()
    changed = True
    while changed:  # peel repeated suffixes: "X Technologies Inc"
        changed = False
        for suffix in CORP_SUFFIXES:
            if n.endswith(suffix):
                n, changed = n[: -len(suffix)].rstrip(), True
    return re.sub(r"\s+", "", n)


def already_applied(company: str, pairs: Iterable[tuple[str, str]]) -> str | None:
    """The matching confirmation's subject, or None. Short names need a word match."""
    key = squash(company)
    if len(key) < 3:
        return None
    for sender, subject in pairs:
        blob = f"{sender} {subject}"
        if key in squash(blob):
            return subject or sender
        # 'N1' or 'Clark' would collide inside a squashed blob, so require a real word.
        if len(key) <= 4 and re.search(rf"\b{re.escape(company)}\b", blob, re.I):
            return subject or sender
    return None


# -------------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=FRESH_DAYS,
                    help=f"freshness window in days (default {FRESH_DAYS})")
    ap.add_argument("--applied-window", type=int, default=APPLIED_WINDOW_DAYS,
                    help=f"Gmail lookback (default {APPLIED_WINDOW_DAYS})")
    ap.add_argument("--no-gmail", action="store_true", help="skip the applied check")
    args = ap.parse_args()

    companies = R.load_companies()
    if not companies:
        print("no companies.txt entries", file=sys.stderr)
        return 1

    # companies.txt is >1200 boards, so fetch them concurrently: serially this would
    # take most of an hour. 12 workers is polite against public ATS endpoints.
    def pull(entry: tuple[str, str, str]) -> list[dict[str, Any]] | None:
        ats, slug, name = entry
        fetch = FETCHERS.get(ats)
        if fetch is None:  # workday and friends carry no first-publish field
            return []
        try:
            return fetch(slug, name)
        except (OSError, KeyError, json.JSONDecodeError, ValueError):
            return None  # OSError covers URLError/HTTPError/socket.timeout

    rows: list[dict[str, Any]] = []
    sources_ok = sources_failed = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        for got in pool.map(pull, companies):
            if got is None:
                sources_failed += 1
            else:
                rows.extend(got)
                sources_ok += 1
    print(f"fetched {len(rows)} live postings from {sources_ok} boards "
          f"({sources_failed} unreachable)")

    drops = {"not swe or senior": 0, "defense or cleared site": 0, "internship": 0, "not us": 0, "clearance": 0,
             "no publish date": 0, "stale": 0, "applied": 0,
             "clearance or ITAR in body": 0, "excludes OPT explicitly": 0,
             "3+ years required": 0}
    kept: list[dict[str, Any]] = []
    for r in rows:
        # is_swe already strips senior/staff/lead/II/III/Sr, so a title with no explicit
        # level word is a plain "Software Engineer, X" and belongs on the list. Dropping
        # those would hide exactly the reqs that read 0-5 years in the body.
        r["level"] = R.tier(r["title"]) or "unlabeled"
        if not R.is_swe(r["title"]):
            drops["not swe or senior"] += 1
        elif (squash(r["company"]) in {squash(d) for d in DEFENSE_EMPLOYERS}
              or R.norm(r["company"]) in R.DEFENSE
              or CLEARED_SITES.search(r["loc"] or "")):
            drops["defense or cleared site"] += 1
        elif INTERNSHIP.search(r["title"]):
            drops["internship"] += 1
        elif NON_US_STRICT.search(r["loc"] or "") or not R.in_us(r["loc"]):
            drops["not us"] += 1
        elif R.clearance_required(f"{r['title']} {r['loc']}"):
            drops["clearance"] += 1
        elif r["published"] is None:
            drops["no publish date"] += 1  # cannot prove freshness, so refuse it
        elif (NOW - r["published"]).days > args.days:
            drops["stale"] += 1
        else:
            kept.append(r)

    # Body pass over the survivors only.
    if kept:
        with ThreadPoolExecutor(max_workers=8) as pool:
            bodies = list(pool.map(body_of, kept))
        survivors = []
        for r, body in zip(kept, bodies):
            # eligibility.assess replaces the inline gates. It adds the graduation-cohort
            # and mandatory-language checks, separates a no-sponsor statement from a named
            # visa-category bar, and returns the sentence that decided each call. Covered
            # by 58 tests built from real postings, in test_eligibility.py.
            v = eligibility.assess(r["title"], r["loc"] or "", body)
            r["verdict"], r["quotes"] = v.decision, v.quotes
            r["cohort"], r["yoe"] = v.cohort, v.years_floor

            # Workday rows entered the body pass on a relative estimate; body_of replaced
            # it with the exact startDate, so re-apply the window now.
            if (NOW - r["published"]).days > args.days:
                drops["older than window (exact date)"] = \
                    drops.get("older than window (exact date)", 0) + 1
                continue
            if v.blocked:
                reason = v.reasons[0] if v.reasons else "blocked"
                key = ("cohort mismatch" if "cohort" in reason
                       else "clearance or ITAR in body" if "clearance" in reason
                       else "excludes OPT explicitly" if "visa" in reason
                       else "off-lane (body)" if "off-lane" in reason
                       else "3+ years required")
                drops[key] = drops.get(key, 0) + 1
                continue
            if v.decision in ("reach", "runway") and r["level"] == "unlabeled":
                r["level"] = "reach"
            r["score"] = archetype.score(
                r["company"], r["title"], r["loc"] or "", body, r["source"],
                (NOW - r["published"]).days if r["published"] else None)
            survivors.append(r)
        kept = survivors

    seen_roles: set[tuple[str, str]] = set()
    deduped = []
    for r in kept:
        k = (R.norm(r["company"]), R.norm(r["title"]))
        if k in seen_roles:
            drops["duplicate row"] = drops.get("duplicate row", 0) + 1
            continue
        seen_roles.add(k)
        deduped.append(r)
    kept = deduped

    # All-time company history WARNS, it does not drop. Applying to a company once does
    # not disqualify a new requisition there: Lyft posted a fresh associate-SWE req three
    # weeks after an earlier application, and dropping on company name would have hidden
    # the best-matched role of the run. Only the mail check, which sees actual
    # confirmations, is allowed to remove a row.
    if APPLIED.exists():
        known = {squash(x) for x in APPLIED.read_text().splitlines()
                 if x.strip() and not x.startswith("#")}
        for r in kept:
            r["seen_before"] = squash(r["company"]) in known

    applied_hits: list[tuple[str, str, str]] = []
    if kept and not args.no_gmail:
        try:
            pairs = applied_recently(args.applied_window)
            print(f"gmail: {len(pairs)} application confirmations in the last "
                  f"{args.applied_window} days")
            survivors = []
            for r in kept:
                hit = already_applied(r["company"], pairs)
                if hit:
                    applied_hits.append((r["company"], r["title"], hit))
                    drops["applied"] += 1
                else:
                    survivors.append(r)
            kept = survivors
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"gmail check skipped ({type(exc).__name__}: {exc})", file=sys.stderr)

    # Explicitly labelled new-grad/entry reqs lead; unlabelled plain SWE titles follow.
    # Archetype tier first: it encodes which employers have actually produced a human
    # response, which matters more than the title's level word.
    tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    kept.sort(key=lambda r: (
        tier_rank.get(r["score"].tier if r.get("score") else "C", 2),
        -(r["score"].points if r.get("score") else 0),
        -r["published"].timestamp()))

    lines = [
        f"# Fresh roles — first published in the last {args.days} days",
        "",
        f"_Generated {NOW:%Y-%m-%d %H:%M} UTC. Every row is live on the employer's own "
        f"ATS board right now, was FIRST published (not merely updated or reposted) "
        f"within {args.days} days, and has no application confirmation in your Gmail from "
        f"the last {args.applied_window} days._",
        "",
        "| Tier | Company | Role | Location | First posted | Apply |",
        "|---|---|---|---|---|---|",
    ]
    def cell(v: str) -> str:
        # A raw pipe inside a value splits the markdown row: Anthropic lists
        # "San Francisco, CA | New York City, NY" and silently gained a column.
        return (v or "—").replace("|", "/").strip()

    for r in kept:
        age = (NOW - r["published"]).days
        when = "today" if age <= 0 else f"{age}d ago"
        mark = " (applied here before)" if r.get("seen_before") else ""
        sc = r.get("score")
        lines.append(f"| **{sc.tier if sc else '?'}** | {cell(r['company'])}{mark} "
                     f"| {cell(r['title'])} | {cell(r['loc'])} | {when} "
                     f"| [apply]({r['url']}) |")
    if not kept:
        lines.append("| — | nothing new in the window | — | — | — | — | — |")

    # The two sentences, quoted per row. Reading them costs 20 seconds and has already
    # been worth three wasted applications (Pariveda rejected him on the graduation
    # window; Texas Sports Academy names STEM OPT as disqualifying; Old Mission is a 2027
    # cohort). An explicit "not stated" is information too.
    if kept:
        lines += ["", "## Read these two sentences before applying", ""]
        for r in kept:
            sc, q = r.get("score"), r.get("quotes", {})
            lines += [
                f"**{cell(r['company'])} — {cell(r['title'])}** "
                f"[tier {sc.tier if sc else '?'}: {sc.archetype if sc else '?'}]",
                f"- work authorisation: {q.get('work authorisation', 'not stated')}",
                f"- graduation window: {q.get('graduation window', 'not stated')}",
                f"- years floor: {r.get('yoe')} | verdict: {r.get('verdict')}",
                f"- why this tier: {'; '.join(sc.rationale) if sc else 'n/a'}",
                f"- prior: {sc.prior_note if sc else 'n/a'}", ""]

    lines += ["", "## What was dropped", "",
              *(f"- {k}: {v}" for k, v in drops.items() if v)]
    if applied_hits:
        lines += ["", "### Already applied (skipped)", ""]
        lines += [f"- {c} — {t}  \n  matched: `{s[:90]}`" for c, t, s in applied_hits]
    lines += ["", "_Aggregator feeds (community listing repos, The Muse) are excluded "
              "here on purpose: their dates are crawl dates and closed reqs linger in "
              "them. Use `radar.py` for the wide net._"]

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(kept)} fresh role(s)")
    print(f"  dropped: {drops}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

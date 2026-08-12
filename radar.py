"""Job radar — pull fresh US software-engineering roles from many sources into one shortlist.

There is no universal "all jobs" firehose (LinkedIn/Indeed block scraping), so this
aggregates three source types instead, each free and legitimate:

  1. ATS public APIs      — Greenhouse / Lever / Ashby / SmartRecruiters (per company)
  2. Open listings.json   — community new-grad repos (SimplifyJobs, vanshb03) already
                            aggregated thousands of US roles into public JSON — one fetch
  3. Aggregator API       — The Muse (native Entry-Level / Software-Engineering filters)

Every role flows through one pipeline of guardrails:
  * US-only (drop international / non-US-remote)
  * EXCLUDE security-clearance / US-citizen-required / ITAR roles (hard no on F-1/OPT) —
    but KEEP "citizen or otherwise authorized to work" EEO boilerplate (OPT is authorized)
  * software-engineering roles only, non-senior; entry-level surfaced first
  * newest-first, stale (> MAX_AGE_DAYS) dropped, de-duplicated across sources

Sponsorship is intentionally NOT filtered — apply to all US roles regardless.

Runs locally (`python radar.py`) or in a GitHub Action on a daily cron.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

COMPANIES = "companies.txt"
OUT = "JOBS.md"
MAX_AGE_DAYS = 45
NOW = datetime.now(timezone.utc)

# Community repos that publish a machine-readable listings.json (raw URLs).
LISTINGS_JSON = {
    "SimplifyJobs": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "vanshb03-2027": "https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/.github/scripts/listings.json",
    "vanshb03-2026": "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/dev/.github/scripts/listings.json",
    "cvrve-2025": "https://raw.githubusercontent.com/cvrve/New-Grad-2025/dev/.github/scripts/listings.json",
}

# Large employers on Workday whose PUBLIC CXS JSON API we poll directly. Workday has no
# master directory, so each company is a distinct {tenant, data-center wd#, site}. All 15
# were curl-verified live (HTTP 200 + populated jobPostings) and are consistent H-1B
# sponsors. URL pattern: https://{tenant}.wd{N}.myworkdayjobs.com/{site}
#   CAVEATS to read per-req: Capital One + Fidelity's LEAP program often say "no sponsorship";
#   Adobe's site is the experienced-hire board (thin on new-grad). See notes when applying.
WORKDAY_TENANTS = {
    "Salesforce":            {"tenant": "salesforce", "wd": "wd12", "site": "External_Career_Site"},
    "NVIDIA":                {"tenant": "nvidia",     "wd": "wd5",  "site": "NVIDIAExternalCareerSite"},
    "Adobe":                 {"tenant": "adobe",      "wd": "wd5",  "site": "external_experienced"},
    "Autodesk":              {"tenant": "autodesk",   "wd": "wd1",  "site": "Ext"},
    "Capital One":           {"tenant": "capitalone", "wd": "wd12", "site": "Capital_One"},
    "PayPal":                {"tenant": "paypal",     "wd": "wd1",  "site": "jobs"},
    "Mastercard":            {"tenant": "mastercard", "wd": "wd1",  "site": "CorporateCareers"},
    "Visa":                  {"tenant": "visa",       "wd": "wd5",  "site": "Visa"},
    "Cisco":                 {"tenant": "cisco",      "wd": "wd5",  "site": "Cisco_Careers"},
    "HPE":                   {"tenant": "hpe",        "wd": "wd5",  "site": "Jobsathpe"},
    "Workday":               {"tenant": "workday",    "wd": "wd5",  "site": "Workday"},
    "Nasdaq":                {"tenant": "nasdaq",     "wd": "wd1",  "site": "Global_External_Site"},
    "Fidelity":              {"tenant": "fmr",        "wd": "wd1",  "site": "FidelityCareers"},
    "Comcast":               {"tenant": "comcast",    "wd": "wd5",  "site": "Comcast_Careers"},
    "Warner Bros Discovery": {"tenant": "warnerbros", "wd": "wd5",  "site": "global"},
    # --- expanded sponsor-friendly tech tenants (site names taken from live feed URLs) ---
    "Palo Alto Networks":    {"tenant": "paloaltonetworks", "wd": "wd5", "site": "panwexternalcareers"},
    "KLA":                   {"tenant": "kla",        "wd": "wd1",  "site": "UR"},
    "Cadence":               {"tenant": "cadence",    "wd": "wd1",  "site": "Univ_Careers"},
    "Motorola Solutions":    {"tenant": "motorolasolutions", "wd": "wd5", "site": "Careers"},
    "Cboe":                  {"tenant": "cboe",       "wd": "wd1",  "site": "External_Career_CBOE"},
    "Cox":                   {"tenant": "cox",        "wd": "wd1",  "site": "Cox_External_Career_Site_1"},
    "NCR Voyix":             {"tenant": "ncr",        "wd": "wd1",  "site": "ext_us"},
    "Rocket (Quicken Loans)":{"tenant": "quickenloans", "wd": "wd5", "site": "rocket_careers"},
    "Litera":                {"tenant": "litera",     "wd": "wd12", "site": "Litera_Careers"},
    "Clearwater Analytics":  {"tenant": "clearwateranalytics", "wd": "wd1", "site": "Clearwater_Analytics_Careers"},
    "Danaher":               {"tenant": "danaher",    "wd": "wd1",  "site": "danaherjobs"},
    "Dexcom":                {"tenant": "dexcom",     "wd": "wd1",  "site": "Dexcom"},
    "Intuitive Surgical":    {"tenant": "intuitive",  "wd": "wd1",  "site": "irtc_careers"},
    "GE Vernova":            {"tenant": "gevernova",  "wd": "wd5",  "site": "vernova_externalsite"},
    "Merck":                 {"tenant": "msd",        "wd": "wd5",  "site": "searchjobs"},
    "Caterpillar":           {"tenant": "cat",        "wd": "wd5",  "site": "CaterpillarCareers"},
    "Chevron":               {"tenant": "chevron",    "wd": "wd5",  "site": "University"},
    "Freddie Mac":           {"tenant": "freddiemac", "wd": "wd5",  "site": "External"},
}
# Entry-oriented search terms — Workday relevance-ranks and floats senior roles to the top,
# so a plain "software engineer" query buries new-grad reqs. Query each term, merge, dedup.
WORKDAY_SEARCHES = ("new grad software engineer", "associate software engineer",
                    "software engineer 1", "graduate software engineer",
                    "junior software developer", "ai engineer")

# LinkedIn search queries, run daily through the rented Apify actor (last-24h window,
# entry-level + full-time facets are applied in the URL builder below).
LINKEDIN_SEARCHES = (
    "software engineer new grad", "entry level software engineer",
    "junior software engineer", "associate software engineer",
    "junior developer", "entry level ai engineer",
    "junior ai engineer", "entry level ai developer", "ai generalist",
)

# ---- what counts as a software-engineering role ----
ROLE = re.compile(
    r"\b(software\s+engineer|software\s+developer|"
    r"software\s+development\s+engineer|back[\s-]?end\s+engineer|"
    r"front[\s-]?end\s+engineer|full[\s-]?stack\s+(engineer|developer)|"
    r"web\s+developer|application\s+developer|platform\s+engineer|"
    r"infrastructure\s+engineer|programmer|swe|sde|"
    r"ai\s+engineer|ai\s+developer|ai\s+generalist|applied\s+ai|"
    r"machine\s+learning\s+engineer|ml\s+engineer|mle|"
    r"generative\s+ai\s+(engineer|developer)|genai\s+(engineer|developer)|"
    r"llm\s+engineer|junior\s+(developer|engineer))\b", re.I)
DROP = re.compile(
    r"(\b(senior|staff|principal|lead|manager|director|head\s+of|vp|"
    r"solutions\s+(engineer|architect)|forward\s+deployed|sales|marketing|"
    r"recruit|II|III|IV|L[4-9])\b|\bsr\b)", re.I)

# ---- entry-level tiers ----
NEWGRAD = re.compile(
    r"\b(new\s?grad|graduate|university\s?grad|campus|early\s?career|"
    r"entry[\s-]?level|university|college\s+grad(uate)?)\b", re.I)
JUNIOR = re.compile(
    r"\b(junior|jr\.?|associate\s+(software|engineer)|"
    r"software\s+engineer\s+[i1]\b|engineer\s+[i1]\b|sde\s?(1|i)\b|"
    r"software\s+development\s+engineer\s+[i1]\b|amts|"
    r"level\s?1|entry)\b", re.I)

# ---- location: US / US-remote only ----
NON_US = re.compile(
    r"(\b(canada|poland|spain|india|brazil|germany|uk|ireland|mexico|"
    r"portugal|netherlands|france|australia|singapore|japan|israel|"
    r"taiwan|china|korea|philippines|malaysia|vietnam|hong\s+kong|"
    r"slovakia|czech\w*|romania|hungary|switzerland|sweden|austria|belgium|"
    r"greece|turkey|thailand|indonesia|argentina|colombia|costa\s+rica|"
    r"toronto|ontario|vancouver|montreal|quebec|alberta|british\s+columbia|"
    r"emea|apac|latam|europe)\b|,\s*(ON|BC|QC|AB)\s*,?\s*CA\b)", re.I)
US_OK = re.compile(
    r"\b(united states|usa|u\.s\.|remote|us[\s-]|, [A-Z]{2}\b|\bSF\b|\bNYC\b|\bLA\b|"
    r"new york|san francisco|seattle|austin|boston|chicago|tempe|phoenix|"
    r"arizona|texas|california|washington)\b", re.I)

# ---- clearance / citizenship / ITAR exclusion (F-1/OPT can't satisfy these) ----
CLEARANCE = re.compile(r"""
(?:
    \bTS \s* [/&-] \s* SCI\b | \bTS \s+ SCI\b | \btop[\s-]secret\b
  | \bsecret[\s-]level\s+clearance\b | \bsecret\s+clearance\b
  | \b(?:DoD|DOD|government|govt|security|interim|active|current)\s+clearance\b
  | \bclearance\s+(?:is\s+)?(?:required|eligib\w*|mandatory)\b
  | \beligib\w*\s+(?:to\s+obtain|to\s+acquire|for)\s+(?:and\s+maintain\s+)?(?:a[n]?\s+)?(?:\w+\s+){0,3}?clearance\b
  | \bability\s+to\s+obtain\s+(?:and\s+maintain\s+)?(?:a[n]?\s+)?(?:\w+\s+){0,3}?clearance\b
  | \bpublic\s+trust\b | \bpolygraph\b
  | \b(?:CI|C\.I\.|counter[\s-]?intel\w*|full[\s-]scope|lifestyle)\s+poly\b
  | \b[QL]\s+clearance\b | \bSCI\s+eligib\w*\b | \bspecial\s+access\s+program\b | \bDoD\s+8570\b
  | \bU\.?\s?S\.?\s+citizen(?:ship)?\b | \bUnited\s+States\s+citizen(?:ship)?\b
  | \bcitizenship\s+(?:is\s+)?required\b | \bUSC\s+only\b
  | \bsole(?:ly)?\s+U\.?\s?S\.?\s+citizen\w*\b | \bU\.?\s?S\.?\s+person\b
  | \bITAR\b | \bITAR\s*/\s*EAR\b | \bEAR[\s-]controlled\b | \bEAR\s+regulation\w*\b
  | \bexport[\s-]control(?:led|s)?\b
)
""", re.IGNORECASE | re.VERBOSE)
# Locations that are effectively clearance-only (military / intel sites) — near-100%
# require a clearance even when the title doesn't say so. Kept tight to avoid over-blocking
# normal metros (Herndon/Reston have plenty of uncleared roles and are NOT listed).
CLEARANCE_LOC = re.compile(
    r"\b(annapolis\s+junction|fort\s+meade|ft\.?\s+meade|fort\s+gordon|fort\s+belvoir|"
    r"fort\s+bragg|quantico|wright[\s-]patterson|hanover,?\s*md|scott\s+afb)\b", re.I)
CITIZENSHIP_ONLY = re.compile(
    r"\bU\.?\s?S\.?\s+citizen(?:ship)?\b|\bUnited\s+States\s+citizen(?:ship)?\b", re.I)
SPONSORSHIP_CTX = re.compile(
    r"(permanent resident|green\s?card|authorized to work|work authorization|"
    r"lawful permanent|visa|sponsor|EEO|equal opportunity|protected veteran)", re.I)
# Pure-defense firms — almost always clearance; hard-exclude by name.
# (Anduril/Palantir/SpaceX deliberately NOT here — they're mixed, filtered by role text.)
DEFENSE = {norm for norm in (
    "lockheed martin", "raytheon", "rtx", "northrop grumman", "general dynamics",
    "gdit", "booz allen", "saic", "leidos", "l3harris", "bae systems", "caci",
    "peraton", "mantech", "mitre", "parsons", "kbr", "sierra nevada", "ball aerospace")}


def get(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": "job-radar/2.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def parse_dt(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def tier(title):
    if NEWGRAD.search(title):
        return "newgrad"
    if JUNIOR.search(title):
        return "entry"
    return None


def is_swe(title):
    return bool(ROLE.search(title)) and not DROP.search(title)


def in_us(loc):
    if NON_US.search(loc or ""):
        return False
    return US_OK.search(loc or "") is not None or (loc or "") == ""


def clearance_required(text):
    """True if the text gates on clearance/citizenship/ITAR — but not mere work-auth boilerplate."""
    text = text or ""
    m = CLEARANCE.search(text)
    if not m:
        return False
    if CITIZENSHIP_ONLY.fullmatch(m.group(0)):
        window = text[max(0, m.start() - 80): m.end() + 80]
        if SPONSORSHIP_CTX.search(window):
            return any(not CITIZENSHIP_ONLY.fullmatch(mm.group(0))
                       for mm in CLEARANCE.finditer(text))
    return True


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ---- sources: each yields normalized {company, title, loc, url, posted, source} ----
def _row(company, title, loc, url, posted, source):
    return {"company": company, "title": title, "loc": loc, "url": url,
            "posted": posted, "source": source}


def greenhouse(slug, name):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false")
    return [_row(name, j["title"], (j.get("location") or {}).get("name", ""),
                 j["absolute_url"], parse_dt(j.get("updated_at")), "greenhouse")
            for j in d.get("jobs", [])]


def lever(slug, name):
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [_row(name, j["text"], (j.get("categories") or {}).get("location", ""),
                 j["hostedUrl"], parse_dt(j.get("createdAt")), "lever") for j in d]


def ashby(slug, name):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [_row(name, j["title"], j.get("location", ""), j["jobUrl"],
                 parse_dt(j.get("publishedAt") or j.get("updatedAt")), "ashby")
            for j in d.get("jobs", [])]


def smartrecruiters(slug, name):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=us&limit=100")
    out = []
    for j in d.get("content", []):
        loc = j.get("location", {})
        locs = ", ".join(x for x in [loc.get("city"), loc.get("country", "").upper()] if x)
        url = f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
        out.append(_row(name, j["name"], locs, url, parse_dt(j.get("releasedDate")),
                        "smartrecruiters"))
    return out


ATS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
       "smartrecruiters": smartrecruiters}


def linkedin():
    """Pull last-24h entry-level postings from LinkedIn via a rented Apify actor.

    Uses the curious_coder/linkedin-jobs-scraper actor (no cookies, public data).
    Auth: the Apify API token in the LINKEDIN_JOB_SCRAPPER env var / repo secret.
    Skips quietly when the token is absent (e.g. a local run without the secret).
    """
    import os
    import time as _time
    import urllib.parse

    token = os.environ.get("LINKEDIN_JOB_SCRAPPER")
    if not token:
        return None  # signal "source not configured"

    # One LinkedIn guest-search URL per query: last 24h (f_TPR=r86400),
    # entry level + internship excluded -> f_E=2, full-time (f_JT=F), US.
    urls = []
    for q in LINKEDIN_SEARCHES:
        urls.append("https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode({
            "keywords": q, "location": "United States",
            "f_TPR": "r86400", "f_E": "2", "f_JT": "F",
        }))
    payload = json.dumps({
        "urls": urls,
        "scrapeCompany": False,
        "limitPerSource": 25,
    }).encode()

    actor = "curious_coder~linkedin-jobs-scraper"
    run = get(f"https://api.apify.com/v2/acts/{actor}/runs?token={token}",
              data=payload, headers={"Content-Type": "application/json"})
    run_id = run["data"]["id"]

    # poll to completion (the scrape takes a few minutes)
    for _ in range(60):
        _time.sleep(15)
        st = get(f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}")
        status = st["data"]["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status != "SUCCEEDED":
        raise RuntimeError(f"apify run {status}")

    ds = st["data"]["defaultDatasetId"]
    items = get(f"https://api.apify.com/v2/datasets/{ds}/items?token={token}&clean=true")

    out, seen = [], set()
    for j in items:
        title = j.get("title") or j.get("jobTitle") or ""
        company = (j.get("companyName") or j.get("company") or
                   (j.get("companyDetails") or {}).get("name", "") or "")
        loc = j.get("location") or j.get("place") or ""
        url = (j.get("link") or j.get("jobUrl") or j.get("url") or
               j.get("applyUrl") or "")
        if not title or not url or url in seen:
            continue
        seen.add(url)
        posted = parse_dt(j.get("postedAt") or j.get("publishedAt") or
                          j.get("listedAt") or j.get("postedTime")) or NOW
        out.append(_row(company, title, loc, url, posted, "linkedin"))
    return out


def workday_posted(text):
    """Workday returns freshness as human text ('Posted 3 Days Ago'), not an ISO date."""
    if not text:
        return None
    t = text.lower()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    elif (m := re.search(r"(\d+)\+?\s*day", t)):
        days = int(m.group(1))
    elif (m := re.search(r"(\d+)\+?\s*month", t)):
        days = int(m.group(1)) * 30
    else:
        return None
    return NOW - timedelta(days=days)


def workday(company, cfg):
    """Poll a company's public Workday CXS JSON API for entry-level SWE roles.

    POST {searchText, limit, offset} -> {total, jobPostings:[{title, locationsText,
    externalPath, postedOn}]}. One POST per entry-oriented search term, merged + deduped.
    """
    base = f"https://{cfg['tenant']}.{cfg['wd']}.myworkdayjobs.com"
    cxs = f"{base}/wday/cxs/{cfg['tenant']}/{cfg['site']}/jobs"
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    out, seen = [], set()
    for term in WORKDAY_SEARCHES:
        for offset in (0, 20):  # two pages — relevance-ranking buries entry roles deep
            body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": offset,
                               "searchText": term}).encode()
            try:
                d = get(cxs, data=body, headers=hdrs)
            except Exception:  # noqa: BLE001 - one flaky page shouldn't kill the company
                continue
            for j in d.get("jobPostings", []):
                path = j.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                loc = (j.get("locationsText") or "").strip()
                # "2 Locations" / "Multiple Locations" (and blanks) hide the real country;
                # recover it from the path segment (/job/US-CA-Santa-Clara/... vs
                # /job/Taiwan-Taipei/...) so the US filter can still do its job.
                if not loc or re.search(r"\d+\s+locations|multiple\s+locations", loc, re.I):
                    m = re.search(r"/job/([^/]+)/", path)
                    if m:
                        loc = m.group(1).replace("-", ", ")
                out.append(_row(company, j.get("title", ""), loc,
                                f"{base}/{cfg['site']}{path}",
                                workday_posted(j.get("postedOn")), "workday"))
    return out


def listings_repo(name, url):
    """Community new-grad listings.json (Simplify/vanshb03 schema)."""
    out = []
    for j in get(url):
        if not (j.get("active") and j.get("is_visible", True)):
            continue
        # their own citizenship marker → treat as clearance exclusion
        if j.get("sponsorship") == "U.S. Citizenship is Required":
            continue
        loc = "; ".join(j.get("locations", []) or [])
        out.append(_row(j.get("company_name", ""), j.get("title", ""), loc,
                        j.get("url", ""), parse_dt(j.get("date_posted")), name))
    return out


def themuse():
    """The Muse — native Entry Level + Software Engineering filters (no key needed)."""
    out = []
    for page in range(3):
        d = get("https://www.themuse.com/api/public/jobs?category=Software%20Engineering"
                f"&level=Entry%20Level&page={page}&descending=true")
        for j in d.get("results", []):
            loc = "; ".join(x.get("name", "") for x in j.get("locations", []))
            out.append(_row((j.get("company") or {}).get("name", ""), j.get("name", ""),
                            loc, (j.get("refs") or {}).get("landing_page", ""),
                            parse_dt(j.get("publication_date")), "themuse"))
    return out


def load_companies():
    out = []
    try:
        with open(COMPANIES, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else parts[1]))
    except FileNotFoundError:
        pass
    return out


def age_days(posted):
    return None if posted is None else (NOW - posted).days


def age_str(days):
    return "—" if days is None else ("today" if days <= 0 else f"{days}d ago")


def table(rows):
    out = ["| Company | Role | Location | Posted | Source | Link |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        loc = (r["loc"] or "").replace("|", "/")
        out.append(f"| {r['company']} | {r['title'].strip()} | {loc} | "
                   f"{age_str(r['_days'])} | {r['source']} | [apply]({r['url']}) |")
    return "\n".join(out)


def main():
    raw, failed, srcs = [], [], 0

    # source 1: ATS APIs (per company)
    for ats, slug, name in load_companies():
        fn = ATS.get(ats)
        if not fn:
            failed.append(f"{name} (unknown ats {ats!r})")
            continue
        try:
            raw += fn(slug, name)
            srcs += 1
        except urllib.error.HTTPError as e:
            failed.append(f"{name} (HTTP {e.code} — check slug {slug!r})")
        except Exception as e:  # noqa: BLE001 - one bad board shouldn't kill the run
            failed.append(f"{name} ({type(e).__name__})")

    # source 2: open community listings.json
    for name, url in LISTINGS_JSON.items():
        try:
            got = listings_repo(name, url)
            raw += got
            srcs += 1
            print(f"  {name:14} {len(got):4} listings")
        except Exception as e:  # noqa: BLE001
            failed.append(f"{name} listings ({type(e).__name__})")

    # source 3: The Muse aggregator
    try:
        got = themuse()
        raw += got
        srcs += 1
        print(f"  {'The Muse':14} {len(got):4} listings")
    except Exception as e:  # noqa: BLE001
        failed.append(f"The Muse ({type(e).__name__})")

    # source 4: Workday CXS boards (big employers) — kept separate, entry-level only
    wday_raw = []
    for company, cfg in WORKDAY_TENANTS.items():
        try:
            got = workday(company, cfg)
            wday_raw += got
            print(f"  {('wd:' + company):18} {len(got):4} postings")
        except Exception as e:  # noqa: BLE001
            failed.append(f"{company} workday ({type(e).__name__})")
    if wday_raw:
        srcs += 1

    # source 5: LinkedIn (last 24h) via rented Apify actor — needs the secret
    li_raw = []
    try:
        got = linkedin()
        if got is None:
            print(f"  {'LinkedIn':14} skipped (no LINKEDIN_JOB_SCRAPPER token)")
        else:
            li_raw = got
            srcs += 1
            print(f"  {'LinkedIn':14} {len(got):4} postings")
    except Exception as e:  # noqa: BLE001
        failed.append(f"LinkedIn via Apify ({type(e).__name__})")

    # ---- guardrails + dedup ----
    entry, other, seen = [], [], set()
    dropped = {"role": 0, "us": 0, "clearance": 0, "stale": 0, "dup": 0}
    for r in raw:
        title, company = r["title"] or "", r["company"] or ""
        if not is_swe(title):
            dropped["role"] += 1
            continue
        if not in_us(r["loc"]):
            dropped["us"] += 1
            continue
        if (norm(company) in DEFENSE or clearance_required(f"{title} {company}")
                or CLEARANCE_LOC.search(r["loc"] or "")):
            dropped["clearance"] += 1
            continue
        r["_days"] = age_days(r["posted"])
        if r["_days"] is not None and r["_days"] > MAX_AGE_DAYS:
            dropped["stale"] += 1
            continue
        key = (norm(company), norm(title))
        if key in seen:
            dropped["dup"] += 1
            continue
        seen.add(key)
        (entry if tier(title) else other).append(r)

    def by_age(r):
        return (r["_days"] is None, r["_days"] if r["_days"] is not None else 10**9)
    entry.sort(key=by_age)
    other.sort(key=by_age)

    # Workday: same guardrails, but keep ONLY entry/new-grad ("our roles") — these boards
    # are dominated by senior reqs, so a non-entry bucket would be all noise.
    wday, wseen = [], set()
    for r in wday_raw:
        title, company = r["title"] or "", r["company"] or ""
        if not is_swe(title) or tier(title) is None:
            continue
        # Workday boards are global; the country is sometimes only in the title
        # ("… - Graduate - Slovakia"), so guard against both title and location.
        if NON_US.search(title) or not in_us(r["loc"]):
            continue
        if clearance_required(f"{title} {company}") or CLEARANCE_LOC.search(r["loc"] or ""):
            continue
        r["_days"] = age_days(r["posted"])
        if r["_days"] is not None and r["_days"] > MAX_AGE_DAYS:
            continue
        key = (norm(company), norm(title), norm(r["loc"]))
        if key in wseen:
            continue
        wseen.add(key)
        wday.append(r)
    wday.sort(key=by_age)

    # LinkedIn: same guardrails; prefer the DIRECT ATS link when we already have the
    # same company+title from a first-party source (LinkedIn's apply flow is worse).
    li, li_seen = [], set()
    for r in li_raw:
        title, company = r["title"] or "", r["company"] or ""
        if not is_swe(title):
            continue
        if NON_US.search(title) or not in_us(r["loc"]):
            continue
        if (norm(company) in DEFENSE or clearance_required(f"{title} {company}")
                or CLEARANCE_LOC.search(r["loc"] or "")):
            continue
        key = (norm(company), norm(title))
        if key in seen or key in li_seen:   # `seen` = ATS/community/Workday rows
            continue
        li_seen.add(key)
        r["_days"] = age_days(r["posted"])
        li.append(r)
    li.sort(key=by_age)

    total = len(entry) + len(other) + len(wday) + len(li)
    lines = [
        f"# Job Radar — {total} US software-engineering roles",
        f"\n_Aggregated from {srcs} sources (company ATS boards + community new-grad "
        f"listings + The Muse). US-only, non-senior, security-clearance roles excluded, "
        f"newest first, older than {MAX_AGE_DAYS} days dropped. Sponsorship not filtered._\n",
        f"## 🎓 Entry-Level / New Grad ({len(entry)})\n",
        table(entry) if entry else "_None titled entry-level right now._",
        f"\n## 🏢 Workday — Big Employers, entry-level ({len(wday)})\n",
        "_Public Workday boards for large, consistent H-1B sponsors (Salesforce, NVIDIA, "
        "Visa, Cisco, PayPal, Mastercard…). SWE-1 / new-grad / associate titles only. "
        "Read each req's work-authorization line — Capital One and Fidelity's LEAP program "
        "often exclude sponsorship._\n",
        table(wday) if wday else "_No entry-level Workday roles surfaced right now._",
        f"\n## 💼 LinkedIn — entry level, last 24h ({len(li)})\n",
        "_Fresh LinkedIn postings (entry-level + full-time facets, posted in the last "
        "24 hours) via a rented scraper. Same guardrails as everything else; when the "
        "same job exists on a first-party board above, the direct link wins and the "
        "LinkedIn row is dropped._\n",
        table(li) if li else "_No fresh LinkedIn roles in the last 24 hours "
        "(or the source was skipped)._",
        f"\n## 💻 Software Engineer — apply to these too ({len(other)})\n",
        table(other) if other else "_None._",
    ]
    if failed:
        lines += ["\n<details><summary>Sources that failed</summary>\n"]
        lines += [f"- {f}" for f in failed] + ["\n</details>"]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {OUT}: {len(entry)} entry + {len(wday)} workday + {len(other)} other "
          f"from {srcs} sources")
    print(f"  dropped: {dropped}")
    if not total and srcs == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

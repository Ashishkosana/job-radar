# job-radar

Aggregates fresh **US software-engineering** roles from many free sources into one
shortlist — filtered for new-grad/backend/full-stack, US-based, non-senior, with
**security-clearance / citizenship-required roles excluded** (can't hold a clearance
on F-1/OPT). Sponsorship is intentionally *not* filtered.

There's no universal "all jobs" firehose (LinkedIn/Indeed block scraping), so this
pulls from three legitimate source types instead:

```
  1. ATS APIs            Greenhouse / Lever / Ashby / SmartRecruiters (per company)
  2. Open listings.json  community new-grad repos (SimplifyJobs, vanshb03) — 1000s of
                         US roles already aggregated into public JSON, one fetch each
  3. Aggregator API      The Muse (native Entry-Level + Software-Engineering filters)
        │
        └─► one pipeline: US-only · exclude clearance · non-senior · newest-first ·
            drop stale (>45d) · de-dupe across sources ─► JOBS.md
```

## Use it

```bash
python radar.py        # writes JOBS.md
```

A GitHub Action (`.github/workflows/radar.yml`) runs it every weekday morning and
commits the refreshed [`JOBS.md`](JOBS.md).

## Add companies (ATS sources)

Find the slug in a company's careers URL and add a line to `companies.txt`:

```
greenhouse      stripe   Stripe    # boards.greenhouse.io/stripe
lever           <slug>   <Name>    # jobs.lever.co/<slug>
ashby           ramp     Ramp      # jobs.ashbyhq.com/ramp
smartrecruiters Wise     Wise      # jobs.smartrecruiters.com/Wise  (case-sensitive!)
```

`radar.py` reports any slug that 404s. The listings.json + Muse sources need no config.

## Tune the filters

Edit the regexes at the top of `radar.py`: `ROLE`/`DROP` (what's a SWE role),
`NEWGRAD`/`JUNIOR` (entry-level tiers), `NON_US`/`US_OK` (location), and the
clearance guardrail (`CLEARANCE`, `CLEARANCE_LOC`, `DEFENSE`).

_Data sources: [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions),
[vanshb03/New-Grad-2027](https://github.com/vanshb03/New-Grad-2027) (MIT), The Muse API._

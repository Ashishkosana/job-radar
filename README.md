# job-radar

Pulls fresh software-engineering openings from my target companies' applicant
tracking systems into one shortlist — filtered for new-grad/backend/full-stack,
US-based, no senior/managerial noise.

There is no universal "all jobs" firehose (LinkedIn/Indeed block scraping). But
**Greenhouse, Lever, and Ashby each expose a free public JSON board API**, so the
model is simple:

```
curated company list (companies.txt)  ×  its ATS API  →  JOBS.md
```

## Use it

```bash
python radar.py        # writes JOBS.md
```

A GitHub Action (`.github/workflows/radar.yml`) runs this every weekday morning
and commits the refreshed [`JOBS.md`](JOBS.md).

## Add a company

Find the slug in their careers URL and add a line to `companies.txt`:

```
greenhouse  stripe   Stripe     # boards.greenhouse.io/stripe
lever       <slug>   <Name>     # jobs.lever.co/<slug>
ashby       ramp     Ramp       # jobs.ashbyhq.com/ramp
```

`radar.py` reports any slug that 404s so you can fix it.

## Tune what counts as relevant

Edit the `KEEP` / `DROP` / `NON_US` regexes at the top of `radar.py`.

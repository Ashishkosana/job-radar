#!/usr/bin/env python3
"""Rank eligible postings by what has actually produced a human response.

Eligibility says whether he *can* apply. This says whether he *should*, and in what order.

The tiers encode measured outcomes from 23 months of his own applications, not a guess at
what a good job looks like. Every tier carries the sample size behind it in `prior_note`,
because the evidence ranges from strong (about 60 employers, zero human reads) to a single
employer (n=1), and a ranking that hides that distinction is dishonest.

The one finding that is strong: a pool of roughly 60 sub-500-person venture-backed startups
on Ashby and Lever absorbed about half his recent volume since 2026-06-01 and produced ONE
automated assessment and ZERO instances of a human saying they had read his file.

The one finding that is weak but actionable: Canonical is the only employer in 23 months
where two different hiring leads, on two different requisitions, wrote to say they had read
his application. That is n=1 employer, so it is a bet, not a law.

    from archetype import score
    s = score(company, title, location, body, ats, days_since_posted)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# Phoenix metro. Mechanism rather than correlation: he lives in Tempe, so relocation
# disappears and many local reqs never raise the work-authorisation question at all.
PHOENIX = re.compile(
    r"\b(phoenix|tempe|scottsdale|chandler|mesa|gilbert|glendale,?\s*az|peoria,?\s*az|"
    r"goodyear|surprise,?\s*az|arizona|\baz\b)", re.I)

# Remote-global, the Canonical shape. A req open worldwide never applies a US work-auth
# knockout, which is why it is the only archetype where his visa status is irrelevant.
REMOTE_GLOBAL = re.compile(
    r"(home[\s-]based|remote[\s-]?(?:first|global|worldwide|anywhere)|work from anywhere|"
    r"fully (?:remote|distributed)|globally distributed|anywhere in the world|"
    r"remote\s*[-,]?\s*(?:global|worldwide|emea and americas))", re.I)

# A written exercise before any call. This is the only process shape with a non-zero
# human-read record for him: both Canonical requisitions opened this way.
WRITTEN_FIRST = re.compile(
    r"(written interview|written (?:assessment|exercise|application)|"
    r"application (?:questions|form) (?:will be|are) (?:reviewed|read)|"
    r"take[\s-]home (?:exercise|assignment) (?:before|prior to)|"
    r"we read every application|answer (?:the )?questions in (?:the|our) application)", re.I)

# Non-tech enterprise hiring off fiscal backfills rather than cohorts. The State Farm and
# IBM shape: a December 2025 graduation date is a fact rather than a knockout.
ENTERPRISE_NONTECH = re.compile(
    r"\b(insurance|bank|banking|credit union|mutual|financial services|"
    r"health(?:care)? system|hospital|payer|pharmaceutical|pharma|life sciences|"
    r"retail(?:er)?|grocery|utility|utilities|energy|manufactur|industrial|"
    r"airline|railroad|logistics|distribution|government|municipal|university)\b", re.I)

# Explicitly early-career-levelled title.
EARLY_CAREER = re.compile(
    r"\b(new\s?grad|graduate|entry[\s-]?level|early[\s-]?career|junior|jr\.?|associate|"
    r"engineer\s*[i1]\b|developer\s*[i1]\b|\bi\b$|university|campus|apprentice)", re.I)

# An unambiguous entry-level declaration in the BODY, which is where it often lives when
# the title is a bare "Software Engineer". Trovy's title carried no level word while its
# body said "This is an entry-level role designed for someone early in their career", and
# scoring on the title alone buried one of his two cleanest level matches.
BODY_ENTRY_LEVEL = re.compile(
    r"(this is an entry[\s-]?level role|entry[\s-]?level (?:role|position) designed|"
    r"designed for (?:someone|candidates?) early in (?:their|your) career|"
    r"graduating (?:20\d\d|in )|new grad(?:uate)? (?:role|position|hire)|"
    r"0\s*(?:to|-|–)\s*[12]\s*years|no (?:prior )?(?:professional )?experience "
    r"(?:required|necessary)|recent (?:graduate|grad)s? (?:are )?(?:welcome|encouraged)|"
    r"we(?:'ll| will) teach you)", re.I)

# The deprioritised pool. Detected by ATS plus startup markers, since those two together
# are what the measured evidence covers.
STARTUP_ATS = {"ashby", "lever"}
STARTUP_MARKERS = re.compile(
    r"\b(seed|series [a-c]\b|\byc\b|y combinator|backed by|founding (?:engineer|team)|"
    r"early[\s-]stage|we(?:'re| are) a small team|first (?:ten|10|20) engineers|"
    r"venture[\s-]backed|stealth)\b", re.I)

# Large tech with a real early-career track but enormous applicant pools.
BIG_TECH = re.compile(
    r"\b(google|meta|amazon|microsoft|apple|netflix|nvidia|openai|anthropic|databricks|"
    r"stripe|airbnb|uber|lyft|snowflake|salesforce|adobe|intuit|linkedin|pinterest|"
    r"roblox|doordash|coinbase|robinhood|figma|notion|datadog|twilio|cloudflare)\b", re.I)


@dataclass
class Score:
    """Where a posting sits in his priority order, and the evidence for that placement."""

    tier: str            # "A" | "B" | "C" | "D"
    points: int
    archetype: str
    rationale: list[str] = field(default_factory=list)
    prior_note: str = ""

    def __str__(self) -> str:
        return f"[{self.tier}] {self.archetype} ({self.points}pts)"


# Tier priors, each stating its real sample size. A string that overstates its evidence
# here would quietly redirect his week, so the wording is part of the contract.
PRIORS = {
    "remote-global-written": (
        "BET, n=1 employer. Canonical is the only employer in 23 months where two "
        "different hiring leads on two different requisitions wrote to say they read his "
        "application. Remote-global reqs also never apply a US work-auth knockout. One "
        "employer is not a law."),
    "phoenix-metro": (
        "MECHANISM, not correlation. He lives in Tempe, so relocation disappears and many "
        "local reqs never raise the visa question. Only about 3% of his 188 applied "
        "companies have real Phoenix engineering presence, so this is under-exploited."),
    "enterprise-nontech": (
        "WEAK EVIDENCE, n=2. State Farm and IBM both advanced him, and both hire off "
        "fiscal backfills rather than cohorts, which means a Dec-2025 graduation is a fact "
        "rather than a knockout. IBM then rejected him, so this is not a strong prior."),
    "midsize-product": (
        "UNTESTED. No measured outcome either way. Included because it sits between the "
        "flooded big-tech funnels and the startup pool that has produced nothing."),
    "big-tech-early-career": (
        "MIXED, n=3. Real early-career tracks exist, and Roblox and IBM both issued "
        "assessments, but both were automated gates that fired within 20 minutes and IBM's "
        "resolved as a rejection. Enormous applicant pools."),
    "startup-under-500": (
        "STRONGEST EVIDENCE IN THE SET, and it is negative. About 60 distinct employers "
        "and 102 messages since 2026-06-01 produced ONE automated assessment and ZERO "
        "instances of a human saying they read his file, while absorbing roughly half his "
        "recent volume. Caveat: part of that volume is only days old, so censoring "
        "explains some of the silence. Cap at 3 to 5 per week."),
    "unclassified": "No archetype matched. Judge it on the job description.",
}


# "We support fully remote arrangements" in a benefits paragraph is not a remote-global
# req. The archetype is a property of the POSTING's location, so the signal is only read
# from the location field plus the first part of the body, and benefits prose is excluded.
REMOTE_BENEFITS_CONTEXT = re.compile(
    r"(benefit|perk|we offer|flexible (?:work|schedule)|work[\s-]life|"
    r"stipend|reimburs|insurance|401\(?k\)?|paid time off|\bpto\b)", re.I)


def _is_remote_global(location: str, body: str) -> bool:
    """True only when the requisition itself is open worldwide."""
    if REMOTE_GLOBAL.search(location or ""):
        return True
    # Scan the opening of the body, where location and eligibility statements live, and
    # skip any match sitting inside benefits prose.
    for m in REMOTE_GLOBAL.finditer((body or "")[:2500]):
        window = body[max(0, m.start() - 160): m.end() + 160]
        if not REMOTE_BENEFITS_CONTEXT.search(window):
            return True
    return False


def score(company: str, title: str, location: str, body: str,
          ats: str = "", days_since_posted: Optional[int] = None) -> Score:
    """Assign a tier, with the reasoning and the honest prior behind it."""
    text = f"{company}\n{title}\n{location}\n{body}"
    loc_and_co = f"{company} {location}"
    rationale: list[str] = []
    points = 0
    archetype = "unclassified"
    tier = "C"

    is_startup_ats = ats.lower() in STARTUP_ATS
    startup_markers = bool(STARTUP_MARKERS.search(text))
    remote_global = _is_remote_global(location, body)
    written_first = bool(WRITTEN_FIRST.search(text))
    phoenix = bool(PHOENIX.search(loc_and_co))
    big_tech = bool(BIG_TECH.search(company))
    enterprise = bool(ENTERPRISE_NONTECH.search(text)) and not is_startup_ats

    # Tier A. Two routes, both with a stated mechanism rather than a correlation.
    if remote_global and written_first:
        tier, archetype, points = "A", "remote-global-written", 100
        rationale.append("remote-global req with a written exercise before any call, the "
                         "only process shape that has produced a human read for him")
    elif phoenix:
        tier, archetype, points = "A", "phoenix-metro", 90
        rationale.append("Phoenix metro: he already lives there, so relocation and often "
                         "the visa question disappear")
    elif remote_global:
        tier, archetype, points = "B", "remote-global-written", 70
        rationale.append("remote-global, so no US work-auth knockout, but no written-first "
                         "stage advertised")
    elif enterprise and EARLY_CAREER.search(title):
        tier, archetype, points = "B", "enterprise-nontech", 65
        rationale.append("non-tech enterprise hiring an explicitly early-career req off "
                         "fiscal backfills rather than a cohort")
    elif big_tech:
        tier, archetype, points = "C", "big-tech-early-career", 40
        rationale.append("large tech with a real early-career track and an enormous pool")
    elif is_startup_ats and startup_markers:
        tier, archetype, points = "D", "startup-under-500", 10
        rationale.append("sub-500 venture-backed startup on Ashby or Lever, the pool with "
                         "zero human reads across about 60 employers")
    elif is_startup_ats:
        tier, archetype, points = "D", "startup-under-500", 15
        rationale.append("Ashby or Lever startup pool; no explicit startup markers, so "
                         "confirm the size before spending an application")
    else:
        tier, archetype, points = "B", "midsize-product", 50
        rationale.append("mid-size product company, untested archetype")

    # Modifiers. These adjust points within a tier and can promote out of D, because a
    # startup with an explicitly entry-level req is a better bet than the pool average.
    if written_first and archetype != "remote-global-written":
        points += 15
        rationale.append("advertises a written exercise before a call, which is the one "
                         "process shape with a non-zero human-read record")

    if EARLY_CAREER.search(title):
        points += 10
        rationale.append("title is explicitly early-career-levelled")
    elif BODY_ENTRY_LEVEL.search(body):
        # Worth more than a levelled title, because a body that spells out the level is
        # harder to misread than a title convention that varies by employer.
        points += 20
        rationale.append("body states the level explicitly even though the title does not")
    else:
        points -= 5
        rationale.append("title carries no level word, so the body decides the real level")

    if days_since_posted is not None:
        if days_since_posted <= 2:
            points += 15
            rationale.append(f"posted {days_since_posted}d ago, inside the first-48-hours "
                             "window")
        elif days_since_posted >= 8:
            points -= 10
            rationale.append(f"posted {days_since_posted}d ago, past the freshness window")

    # A tier-D req that is unambiguously entry-level is worth more than the pool average.
    # interface.ai ("Graduating 2024 - December 2026") and Trovy ("This is an entry-level
    # role designed for someone early in their career") are the reason this exists: burying
    # his two cleanest level matches on a pool-level prior would be a false economy.
    if tier == "D" and (points >= 35 or BODY_ENTRY_LEVEL.search(body)):
        tier = "C"
        rationale.append("promoted out of the deprioritised pool: the req is explicitly "
                         "entry-level and fresh, which outweighs the pool-level prior")

    return Score(tier=tier, points=points, archetype=archetype, rationale=rationale,
                 prior_note=PRIORS[archetype])


def main(argv: list[str]) -> int:
    samples = [
        ("Canonical", "Junior Software Engineer", "Home based - Worldwide",
         "You will complete a written interview as the first stage.", "greenhouse", 1),
        ("Agate Software", "Software Developer I", "Glendale, AZ", "SQL heavy.", "lever", 2),
        ("State Farm", "Software Engineer I", "Bloomington, IL",
         "A leading insurance company.", "icims", 4),
        ("Trovy", "Software Engineer", "New York, NY",
         "This is an entry-level role designed for someone early in their career. "
         "Series A, backed by top investors.", "ashby", 1),
        ("Gumloop", "Full Stack Software Engineer", "San Francisco, CA",
         "Seed stage, small team, YC backed.", "ashby", 6),
        ("Roblox", "Software Engineer, Early Career", "San Mateo, CA", "", "greenhouse", 3),
    ]
    for company, title, loc, body, ats, age in samples:
        s = score(company, title, loc, body, ats, age)
        print(f"{s}  {company} - {title}")
        for r in s.rationale:
            print(f"      {r}")
        print(f"      prior: {s.prior_note[:110]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

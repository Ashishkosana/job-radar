#!/usr/bin/env python3
"""Decide whether a posting is genuinely open to this candidate, and show the evidence.

Every gate here exists because a specific real application was wasted. The comment above
each pattern names the posting that motivated it, so a future reader can tell a
load-bearing rule from a guess.

Two asymmetries drive the whole design.

The first is that a FALSE NEGATIVE is far more expensive than a false positive. Hiding a
viable job costs an opportunity that is never discovered; surfacing a bad one costs about
twenty seconds of reading. So every gate is written to fire only on requirement language,
and anything ambiguous passes with a flag rather than being blocked.

The second is that "we do not sponsor" and "we cannot consider candidates on OPT" look
similar and mean opposite things. The first is a judgment call the candidate is entitled to
make, since he is authorized today. The second bars him outright. Conflating them either
hides applyable work or wastes applications, so they are separate verdicts.

    from eligibility import assess
    v = assess(title, location, body)
    if v.decision == "blocked": ...

    python3 eligibility.py <file>      # verdict for a saved job description
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# The candidate's graduation. A posting scoped to a different window is a knockout even
# when the level fits: Pariveda rejected him for exactly this.
GRAD_YEAR = 2025
GRAD_MONTH = 12

# ---------------------------------------------------------------------------- utilities

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# A sentence boundary that survives job-description prose, where bullet lists rarely end in
# periods. Newlines and bullet glyphs count as terminators.
_SENTENCE = re.compile(r"[^.!?\n•·]+[.!?]?")


def clean(text: str) -> str:
    """HTML to a single flat string, keeping newlines as sentence boundaries.

    Entities are unescaped BEFORE tags are stripped. Some ATS bodies arrive
    double-encoded, so the markup reads as "&lt;p&gt;" and a tag-stripping pass finds
    nothing to strip; a Truveta work-authorisation quote rendered as raw markup because
    of it. Unescaping twice is safe and covers both encodings.
    """
    text = html.unescape(html.unescape(text or ""))
    text = _TAG.sub(" ", text)
    text = text.replace("\xa0", " ")
    # Unicode hyphens (Truveta writes "F‑1 OPT" with U+2011) must read as ASCII "-",
    # or the f-?1 visa patterns silently never match.
    text = text.replace("‐", "-").replace("‑", "-").replace("­", "")
    return re.sub(r"[ \t]+", " ", text)


def sentence_around(text: str, pos: int) -> str:
    """The sentence containing `pos`, for quoting evidence back to the reader."""
    start = max(text.rfind(".", 0, pos), text.rfind("\n", 0, pos),
                text.rfind("•", 0, pos)) + 1
    end = len(text)
    for term in (".", "\n", "•"):
        found = text.find(term, pos)
        if found != -1:
            end = min(end, found + (1 if term == "." else 0))
    return _WS.sub(" ", text[start:end]).strip()[:400]


# ------------------------------------------------------------------ gate 1: eligibility

# Clearance and citizenship. "Top-Secret" hyphenated slipped an earlier \btop secret\b
# pattern and put an ENSCO req requiring TS/SCI plus 8 years onto a recommendation list.
CLEARANCE = re.compile(
    r"(security clearance|\btop[\s-]?secret\b|\bts/sci\b|\bsci\b eligib|active secret|"
    r"\bpoly(graph)?\b|\bq clearance\b|\bl clearance\b|"
    r"must be a u\.?s\.? citizen|u\.?s\.? citizenship (?:is )?required|"
    r"citizenship is required|\bus citizens? only\b|"
    r"itar\b|international traffic in arms|export[\s-]control|"
    r"\bu\.?s\.? person(s)?\b)", re.I)

# The EEO paragraph lists citizenship as a PROTECTED CLASS, which is the opposite of a
# requirement. SpruceID was nearly discarded over this exact sentence, so a clearance hit
# inside EEO boilerplate is suppressed.
EEO_CONTEXT = re.compile(
    r"(equal (employment )?opportunity|regardless of|without regard to|"
    r"protected (class|status|characteristic)|non[\s-]?discriminat|"
    r"affirmative action|\beeo\b|veteran status|gender identity)", re.I)

# "Some client work may require a clearance" is a possibility, not a bar. A hedge in the
# same sentence downgrades the hit to a flag.
CLEARANCE_HEDGE = re.compile(
    r"(may (be )?requir|might requir|some (client|customer|project|role|position)|"
    r"certain (client|customer|project|role|position)|if (required|applicable)|"
    r"preferred|\bplus\b|nice to have|desirable|willing(ness)? to obtain|"
    r"ability to obtain)", re.I)

# Requirement-phrased sponsorship language: his honest answer to the future-tense form is
# "yes I will need sponsorship", which disqualifies him.
SPONSORSHIP_REQUIRED = re.compile(
    # [^\n] not [^.\n]: "U.S." embeds periods, and requiring a period-free run made
    # Epic's "Eligible to work in the U.S. without visa sponsorship" unmatchable.
    r"((?:authoriz|eligib)\w*[^\n]{0,80}without[^\n]{0,40}sponsorship[^\n]{0,60}"
    r"(?:now or in the future|now and in the future|future)|"
    r"(?:will )?not (?:now or in the future )?requir\w*[^.\n]{0,40}sponsorship|"
    r"must not requir\w*[^.\n]{0,30}sponsorship|"
    r"permanent(?:ly)? authoriz\w*[^.\n]{0,40}without[^.\n]{0,30}sponsorship|"
    # Duration-phrased, and it never says "sponsorship" at all. Energy Solutions
    # (Greenhouse req 5201891007, first published 2026-08-26): "A drug screen and
    # authorization to work in the U.S. indefinitely are preconditions of
    # employment." OPT plus a STEM extension is authorization with an end date,
    # so "indefinitely" / "permanently" / "unrestricted" / "no end date" all mean
    # the same disqualifying thing. Every gate above keyed on the word
    # "sponsorship" and this req therefore reached FRESH.md at tier B, six times,
    # once per posted city.
    r"(?:authoriz|eligib)\w*[^\n]{0,60}\bwork\b[^\n]{0,60}"
    r"(?:indefinitely|permanently|on a permanent basis|with no end date|"
    r"without (?:any )?(?:time |duration )?restriction))", re.I)

# Named exclusion of his visa category. Four real forms:
# Quantcast "not currently able to engage candidates on OPT"; PNC "will not provide
# sponsorship ... or participate in STEM OPT"; Texas Sports Academy listing OPT, CPT and
# STEM OPT extensions as disqualifying in the advance email itself; Truveta (Greenhouse
# req 6140479004, 2026-08-12) "unable to sponsor work visas or permits (e.g., F-1 OPT,
# H-1B)" — a sponsor-refusal sentence that names OPT flips from a runway play into a
# category exclusion, because it tells him his status is what they are refusing.
VISA_CATEGORY_BARRED = re.compile(
    r"(not\s+(?:currently\s+)?(?:be\s+)?able to (?:engage|hire|consider|accept)"
    r"[^.\n]{0,60}\b(?:opt|cpt|f-?1)\b|"
    r"cannot[^.\n]{0,50}\b(?:opt|cpt|f-?1)\b|"
    r"will not[^.\n]{0,90}\b(?:opt|cpt|stem opt)\b|"
    r"(?:not|ineligible|excluded)[^.\n]{0,50}participat\w*[^.\n]{0,50}\bopt\b|"
    r"\b(?:opt|cpt|stem opt)\b[^.\n]{0,50}(?:are|is) not (?:eligible|accepted|considered|"
    r"permitted|able)|"
    r"(?:do(?:es)? not|unable to) (?:accept|consider)[^.\n]{0,40}\b(?:opt|cpt)\b|"
    # The gap after "sponsor" tolerates abbreviation dots ("e.g.," "i.e.,") without
    # crossing a real sentence end: a dot only passes when followed by a lowercase
    # letter, comma, or closing paren.
    r"(?:do(?:es)? not|will not|unable to|cannot)[^.\n]{0,40}sponsor"
    r"(?:[^.\n]|\.(?=[a-z),])){0,80}\b(?:opt|cpt)\b|"
    r"no\s+(?:opt|cpt|f-?1)\b[^.\n]{0,30}(?:candidates|applicants|students))", re.I)

# A plain statement that the employer does not sponsor. NOT a knockout: he is authorized
# today, so this is a runway play and his call. Kept separate on purpose.
NO_SPONSOR_STATEMENT = re.compile(
    r"((?:do(?:es)? not|will not|unable to|cannot|are not able to)[^.\n]{0,40}"
    r"(?:offer|provide|sponsor|transfer)[^.\n]{0,40}"
    r"(?:visa|sponsorship|immigration)|"
    r"(?:visa )?sponsorship is not (?:available|offered|provided)|"
    r"not eligible for (?:visa )?sponsorship|no visa sponsorship)", re.I)


# ---------------------------------------------------------------- gate 2: years of experience

# Digit form. Bounded to 20 because larger numbers are always company history.
YEARS_DIGIT = re.compile(
    r"(\d{1,2})\s*(?:\+|to|-|–|or more)?\s*(?:\d{1,2})?\s*\+?\s*year", re.I)
WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# U.S. Bank titled a req "Software Engineer 1" and asked for "Two to three years of
# relevant experience". A digits-only reader called it entry-level.
YEARS_WORD = re.compile(
    r"\b(" + "|".join(WORD_NUM) + r")\b(?:\s*(?:to|-|–|or more|\+)?\s*"
    r"(?:" + "|".join(WORD_NUM) + r")?\s*)?\s*year", re.I)

# A year count near one of these is a requirement rather than trivia.
EXPERIENCE_CONTEXT = re.compile(
    r"(experience|\bexp\b|background|working|professional|industry|building|developing|"
    r"hands.on|track record|minimum|qualificat|requir|proficien|expertise|"
    r"you (?:have|bring)|seeking|looking for)", re.I)

# Real false positives measured over 83 saved job descriptions: company-age boilerplate
# ("For over 20 years, Agate Software has been..."), the legal minimum ("18+ years of
# age"), benefits ("20 days of paid leave"), and narrative prose in tailoring notes.
YEARS_NOT_EXPERIENCE = re.compile(
    r"(years? of age|\bage\b|for (?:over|more than|nearly) \d+ years|"
    r"(?:founded|since|established|celebrat)\w*\b|\d+ years? (?:ago|stale|old)|"
    r"years? (?:of )?(?:paid|vacation|leave|service award|tenure bonus)|"
    r"\d+ years? in (?:business|operation)|"
    r"over the (?:past|last) \d+ years|"
    r"vesting|401\(?k\)?|"
    # "four year degree" is a programme length, not experience. Scoped tightly so the
    # ubiquitous "Bachelor's degree ... N years of experience" pairing still counts.
    r"\d+[\s-]?year degree|year (?:degree|program|university|college))", re.I)


# -------------------------------------------------------------- gate 3: graduation cohort

# Cohort language, which nothing previously detected. Pariveda rejected him because their
# window was "Dec 2026 to Aug 2027"; Belvedere ("Entry Level 2027"), Freeform ("New Grad
# December 2026"), DV Trading ("by Summer 2027") and Old Mission were all the same miss.
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
_MON = "|".join(MONTHS) + "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_SEASON = r"spring|summer|fall|autumn|winter"

COHORT_RANGE = re.compile(
    r"graduat\w*[^.\n]{0,40}between\s+((?:" + _MON + r")\w*\s*\d{4})\s*(?:and|-|–|to)\s*"
    r"((?:" + _MON + r")\w*\s*\d{4})", re.I)
COHORT_SPAN = re.compile(
    r"graduat\w*[^.\n]{0,30}((?:" + _MON + r")\w*\.?\s*\d{4})\s*(?:-|–|to|and)\s*"
    r"((?:" + _MON + r")\w*\.?\s*\d{4})", re.I)
COHORT_SINGLE = re.compile(
    r"(?:graduat\w*|degree|class of|program for|designed for)[^.\n]{0,60}?"
    r"(?:by|in|during|of)?\s*((?:" + _MON + r"|" + _SEASON + r")\w*\.?\s+)?(20\d\d)\b", re.I)
COHORT_TITLE_YEAR = re.compile(
    # Both orders, because employers write it both ways. Roblox titles its req
    # "[2027] Software Engineer, Early Career", where the year leads and "early career"
    # trails it by 21 characters, so both the keyword list and the gap had to widen.
    r"(?:new\s?grad|graduate|entry[\s-]?level|early[\s-]?career|campus|university|"
    r"cohort|class)[^\n]{0,40}\b(20\d\d)\b"
    r"|\b(20\d\d)\b[^\n]{0,40}"
    r"(?:new\s?grad|graduate|start|cohort|program|entry[\s-]?level|early[\s-]?career|"
    r"class)", re.I)
MUST_BE_ENROLLED = re.compile(
    r"(must be (?:currently )?enrolled|currently (?:enrolled|pursuing)[^.\n]{0,40}degree|"
    r"returning to school|rising (?:junior|senior)|"
    r"enrolled in[^.\n]{0,30}(?:degree|program)[^.\n]{0,40}(?:during|throughout))", re.I)

# A four-digit year is usually not a cohort. Copyright lines, salary years, roadmaps and
# fiscal years all trip a naive reader.
YEAR_NOT_COHORT = re.compile(
    r"(copyright|©|all rights reserved|fiscal (?:year|20)|\bfy\s?20|"
    r"roadmap|founded|since 20|scholarship|conference|award|revenue|"
    r"as of 20|effective 20|posted|updated)", re.I)


# ------------------------------------------------------------- gate 4: mandatory languages

ABSENT_LANGUAGES = ("java", "golang", "go", "rust", "c#", ".net", "kotlin", "scala",
                    "ruby", "php", "swift", "objective-c", "perl", "elixir", "erlang",
                    "haskell", "clojure", "mumps", "cache", "cobol", "abap")
HAS_LANGUAGES = ("python", "typescript", "javascript", "sql", "bash", "shell")

# "Proficiency in Java" is mandatory. "One or more of Python, Java, JavaScript" is
# satisfiable, and Trovy's "at least one backend language (Kotlin, Java, Python, Go,
# TypeScript. We use Kotlin, but we'll teach you.)" is satisfiable AND taught. Mistaking
# the second for the first would have hidden two of his best level matches.
ONE_OF_MANY = re.compile(
    r"(one or more|at least one|one of the following|such as|for example|e\.?g\.?|"
    r"including|like|any of|or similar|or equivalent|and/or|we'?ll teach|will teach|"
    r"we teach|no (?:prior )?experience (?:required|necessary)|willing to learn|"
    r"comfortable with at least one)", re.I)
MANDATORY_LANG = re.compile(
    r"(?:strong |deep |expert |proven |demonstrated )?"
    r"(?:proficien\w+|expertise|fluen\w+|mastery|strong (?:command|knowledge)|"
    r"advanced (?:knowledge|skills?)|extensive experience)\s*"
    r"(?:in|with|of)\s+([A-Za-z0-9#+.\s,/]{0,60})", re.I)


# ------------------------------------------------------------------------ gate 5: lane

OFF_LANE = {
    "embedded": re.compile(
        r"\b(embedded (?:systems?|software|c\b|linux|development)|firmware|\brtos\b|"
        r"microcontroller|\bmcu\b|\bfpga\b|verilog|vhdl|bare[\s-]metal|"
        r"device driver|board support package|\bbsp\b|can bus|\bj1939\b)", re.I),
    "robotics/vision": re.compile(
        r"\b(robotics? (?:software|engineer|perception)|perception (?:stack|pipeline|system)|"
        r"computer vision|\blidar\b|point cloud|sensor fusion|motion planning|slam\b|"
        r"6[\s-]?dof|object tracking|autonomous (?:vehicle|driving) stack)", re.I),
    "hardware/manufacturing": re.compile(
        r"\b(cnc\b|\bcam\b programm|machinist|fixturing|\bgd&t\b|tolerance stack|"
        r"pcb (?:design|layout)|schematic capture|hardware (?:design|bring[\s-]?up))", re.I),
    "qa/sdet": re.compile(
        r"\b(software (?:development )?engineer in test|\bsdet\b|qa (?:engineer|analyst)|"
        r"test (?:automation engineer|analyst)|quality assurance engineer)", re.I),
    "it-admin": re.compile(
        r"\b(system administrator|sysadmin|help ?desk|desktop support|"
        r"active directory|\bentra\b|\bm365\b|office 365|intune|powershell script|"
        r"\bitil\b|service desk|\bsccm\b)", re.I),
    "low-code": re.compile(
        r"\b(servicenow (?:developer|platform|admin)|power (?:platform|apps|automate)|"
        r"salesforce (?:admin|developer)|sharepoint|\blansa\b|oracle apex|"
        r"low[\s-]?code|no[\s-]?code|\brpa\b|uipath|blue prism)", re.I),
    "quant-research": re.compile(
        r"\b(quantitative (?:research|analyst|trader)|alpha (?:research|signal)|"
        r"stochastic calculus|derivatives pricing|market microstructure)", re.I),
    "network-appliance": re.compile(
        r"\b(zscaler|palo alto networks firewall|forward and reverse prox|"
        r"f5 (?:big-?ip|ltm)|cisco (?:ios|asa)|bgp\b|ospf\b)", re.I),
}


# Terms that identify a lane on their own. Presence of any one of these is not a passing
# mention: no backend role requires CAN bus knowledge or Appium.
LANE_DECISIVE = re.compile(
    r"\b(j1939|can bus|firmware|\brtos\b|microcontroller|\bmcu\b|\bfpga\b|verilog|vhdl|"
    r"bare[\s-]metal|device driver|embedded c\b|embedded systems?|"
    r"\blidar\b|point cloud|sensor fusion|motion planning|computer vision|"
    r"perception (?:stack|pipeline|system)|robotics? (?:software|perception)|"
    r"cnc\b|\bcam\b programm|machinist|\bgd&t\b|"
    r"servicenow (?:developer|platform|admin)|power (?:platform|apps|automate)|"
    r"\blansa\b|oracle apex|\brpa\b|uipath|"
    r"appium|\bsdet\b|software (?:development )?engineer in test|"
    r"active directory|\bentra\b|\bm365\b|intune|\bitil\b|\bsccm\b|"
    r"zscaler|quantitative (?:research|analyst)|stochastic calculus)", re.I)


# A lane term inside a sentence describing the BREADTH of a company's work is not a
# requirement. Canonical's grad req says "We work from the very lowest levels of the Linux
# system - BIOS and boot loaders, firmware, kernel and drivers, all the way up to desktop
# applications and containers", and a single decisive "firmware" blocked the best role on
# his board. Scope language suppresses the hit.
LANE_SCOPE_CONTEXT = re.compile(
    r"(all the way (?:up|down|from)|from the (?:very )?(?:lowest|highest|top|bottom)|"
    r"(?:wide )?(?:variety|range) of|everything from|spanning|across the (?:stack|spectrum)|"
    r"we (?:work|build|have teams)|our (?:teams|engineers|products) (?:work|span)|"
    r"anywhere (?:from|between)|whether you|as well as|among (?:them|others))", re.I)


# ------------------------------------------------------------------------------- verdict


@dataclass
class Verdict:
    """Why a posting is or is not open to him, with the sentence that decided it."""

    decision: str  # "eligible" | "runway" | "reach" | "blocked"
    reasons: list[str] = field(default_factory=list)
    quotes: dict[str, str] = field(default_factory=dict)
    years_floor: Optional[int] = None
    cohort: Optional[str] = None
    flags: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.decision == "blocked"

    def summary(self) -> str:
        head = f"{self.decision.upper()}"
        if self.reasons:
            head += "  " + ", ".join(self.reasons)
        return head


def _years_floor(text: str) -> tuple[Optional[int], Optional[str]]:
    """Lowest years-of-experience requirement stated, with the sentence that stated it.

    Deliberately the MINIMUM across all mentions: a posting asking "3+ years backend,
    1+ year Python" really wants one, and hiding an entry-level req is the expensive
    failure.
    """
    best: Optional[int] = None
    quote: Optional[str] = None
    for pattern, convert in ((YEARS_DIGIT, lambda m: int(m.group(1))),
                             (YEARS_WORD, lambda m: WORD_NUM[m.group(1).lower()])):
        for m in pattern.finditer(text):
            try:
                value = convert(m)
            except (KeyError, ValueError):
                continue
            if value > 20:
                continue
            sentence = sentence_around(text, m.start())
            # Same-sentence only. A wider window let the benefits paragraph ("401(k)",
            # "for over 30 years") veto a genuine requirement two sentences earlier.
            if YEARS_NOT_EXPERIENCE.search(sentence):
                continue
            window = text[max(0, m.start() - 110): m.end() + 110]
            # A low number needs corroboration ("one year" appears in benefits prose).
            # A high number does not: nothing benign says "6+ years" in a requirements
            # list, and CPI Security's "6+ years with our core technologies" carried no
            # experience keyword at all.
            if value < 3 and not EXPERIENCE_CONTEXT.search(window):
                continue
            if best is None or value < best:
                best, quote = value, sentence_around(text, m.start())
    return best, quote


def _cohort_window(text: str) -> tuple[Optional[str], bool, Optional[str]]:
    """(window as written, excludes_him, quote).

    Returns excludes_him=True only when a window is stated AND December 2025 falls
    outside it. Ambiguity passes, because interface.ai's real window was
    "Graduating 2024 - December 2026", which includes him.
    """
    def month_year(raw: str) -> tuple[Optional[int], Optional[int]]:
        raw = raw.strip().lower().rstrip(".")
        year = re.search(r"(20\d\d)", raw)
        mon = None
        for name, num in MONTHS.items():
            if raw.startswith(name[:3]):
                mon = num
                break
        if mon is None:
            for season, num in (("spring", 5), ("summer", 8), ("fall", 12),
                                ("autumn", 12), ("winter", 3)):
                if season in raw:
                    mon = num
                    break
        return (int(year.group(1)) if year else None), mon

    def as_months(y: int, m: Optional[int], default: int) -> int:
        return y * 12 + (m if m else default)

    him = as_months(GRAD_YEAR, GRAD_MONTH, GRAD_MONTH)

    for pattern in (COHORT_RANGE, COHORT_SPAN):
        for m in pattern.finditer(text):
            window = sentence_around(text, m.start())
            if YEAR_NOT_COHORT.search(window):
                continue
            y1, m1 = month_year(m.group(1))
            y2, m2 = month_year(m.group(2))
            if not (y1 and y2):
                continue
            low, high = as_months(y1, m1, 1), as_months(y2, m2, 12)
            spoken = f"{m.group(1)} to {m.group(2)}"
            return spoken, not (low <= him <= high), window

    # A single year is only a cohort when it is attached to graduation or programme
    # language and is not the current or a past year used descriptively.
    for pattern in (COHORT_SINGLE, COHORT_TITLE_YEAR):
        for m in pattern.finditer(text):
            window = sentence_around(text, m.start())
            if YEAR_NOT_COHORT.search(window):
                continue
            years = [int(g) for g in m.groups() if g and re.fullmatch(r"20\d\d", g)]
            if not years:
                continue
            year = years[0]
            if year <= GRAD_YEAR:
                continue  # a window opening at or before his graduation cannot exclude him
            if not re.search(r"graduat|class of|cohort|program|start(?:ing)?|new\s?grad|"
                             r"entry[\s-]?level|early[\s-]?career", window, re.I):
                continue
            return str(year), True, window

    m = MUST_BE_ENROLLED.search(text)
    if m:
        return "must be currently enrolled", True, sentence_around(text, m.start())
    return None, False, None


def _mandatory_absent_language(text: str) -> tuple[Optional[str], Optional[str]]:
    """A language he lacks, stated as mandatory rather than one-of-several."""
    for m in MANDATORY_LANG.finditer(text):
        listed = m.group(1).lower()
        window = text[max(0, m.start() - 90): m.end() + 140]
        if ONE_OF_MANY.search(window):
            continue
        if any(h in listed for h in HAS_LANGUAGES):
            continue  # a language he has satisfies the clause
        for lang in ABSENT_LANGUAGES:
            if re.search(r"\b" + re.escape(lang) + r"\b", listed):
                return lang, sentence_around(text, m.start())
    return None, None


def assess(title: str, location: str, body: str) -> Verdict:
    """Decide whether this posting is open to him, and quote the deciding sentence."""
    text = clean(f"{title}\n{location}\n{body}")
    v = Verdict(decision="eligible")

    # 1. Clearance, citizenship, ITAR. Hard, and unfixable by any amount of fit.
    for m in CLEARANCE.finditer(text):
        window = sentence_around(text, m.start())
        if EEO_CONTEXT.search(window):
            continue  # "regardless of ... citizenship" is a promise, not a requirement
        if CLEARANCE_HEDGE.search(window):
            v.flags.append(f"clearance mentioned conditionally: {m.group(0)!r}")
            v.quotes.setdefault("clearance (conditional)", window)
            continue
        v.decision = "blocked"
        v.reasons.append(f"clearance/citizenship: {m.group(0).lower()}")
        v.quotes["clearance"] = window
        return v

    # 2. Named exclusion of his visa category. Checked BEFORE the softer no-sponsor
    #    statement, because PNC's sentence matches both and only the bar is decisive.
    m = VISA_CATEGORY_BARRED.search(text)
    if m:
        v.decision = "blocked"
        v.reasons.append("visa category excluded by name")
        v.quotes["work authorisation"] = sentence_around(text, m.start())
        return v

    # 3. Requirement-phrased "without sponsorship now or in the future".
    m = SPONSORSHIP_REQUIRED.search(text)
    if m:
        v.decision = "blocked"
        v.reasons.append("requires no-sponsorship now or in future")
        v.quotes["work authorisation"] = sentence_around(text, m.start())
        return v

    # 4. Graduation cohort.
    window_text, excludes, quote = _cohort_window(text)
    v.cohort = window_text
    if quote:
        v.quotes["graduation window"] = quote
    if excludes:
        v.decision = "blocked"
        v.reasons.append(f"cohort mismatch: wants {window_text}, he is Dec 2025")
        return v

    # 5. Off-lane.
    for lane, pattern in OFF_LANE.items():
        m = pattern.search(text)
        if not m:
            continue
        in_title = bool(pattern.search(clean(title)))
        quote = sentence_around(text, m.start())
        # One decisive term is enough. J1939 appears in exactly one kind of job, and so
        # does "ServiceNow platform"; requiring three occurrences let both through.
        decisive = bool(LANE_DECISIVE.search(m.group(0)))
        if LANE_SCOPE_CONTEXT.search(quote):
            # Describing what the company works on, not what the role requires.
            v.flags.append(f"{lane} named in a scope description, not a requirement: "
                           f"{m.group(0).lower()}")
            continue
        if in_title or decisive or len(pattern.findall(text)) >= 2:
            v.decision = "blocked"
            v.reasons.append(f"off-lane: {lane} ({m.group(0).lower()})")
            v.quotes["lane"] = quote
            return v
        v.flags.append(f"{lane} mentioned once: {m.group(0).lower()}")

    # 6. Years of experience.
    floor, quote = _years_floor(text)
    v.years_floor = floor
    if quote:
        v.quotes["experience"] = quote
    if floor is not None and floor >= 3:
        v.decision = "blocked"
        v.reasons.append(f"years_floor={floor}")
        return v

    # 7. A mandatory language he does not have. Downgrade, never block: employers who
    #    teach their stack are exactly where a new grad gets hired.
    lang, quote = _mandatory_absent_language(text)
    if lang:
        v.decision = "reach"
        v.reasons.append(f"mandatory language he lacks: {lang}")
        if quote:
            v.quotes["language"] = quote

    if floor == 2 and v.decision == "eligible":
        v.decision = "reach"
        v.reasons.append("years_floor=2")

    # 8. A plain no-sponsor statement. Last, because it is a judgment call rather than a
    #    bar, and it must not mask a harder reason found above.
    m = NO_SPONSOR_STATEMENT.search(text)
    if m and not v.blocked:
        if v.decision == "eligible":
            v.decision = "runway"
        v.reasons.append("employer states it does not sponsor")
        v.quotes.setdefault("work authorisation", sentence_around(text, m.start()))

    if "work authorisation" not in v.quotes:
        v.quotes["work authorisation"] = "not stated"
    if "graduation window" not in v.quotes:
        v.quotes["graduation window"] = "not stated"
    return v


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    raw = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    v = assess("", "", raw)
    print(v.summary())
    print(f"  years_floor : {v.years_floor}")
    print(f"  cohort      : {v.cohort}")
    for gate, quote in v.quotes.items():
        print(f"  {gate:20} {quote[:150]}")
    for flag in v.flags:
        print(f"  flag: {flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Tests for the eligibility gates, built from real postings read this week.

Every case is quoted from an actual job description. The MUST-PASS half matters more than
the MUST-BLOCK half: a false positive costs twenty seconds of reading, a false negative
hides a job he could have got and he never learns it happened.

    python3 -m pytest test_eligibility.py -q
"""

from __future__ import annotations

import pytest

from eligibility import assess

# Realistic surrounding prose, so the negative-context guards get a fair test rather than
# matching a bare fragment.
PREAMBLE = (
    "About the role. We are a growing engineering team building products used by "
    "thousands of customers every day. You will work across the stack with product "
    "managers and designers, ship features to production, and participate in code "
    "review. Our stack includes Python, TypeScript and PostgreSQL on AWS.\n"
)
BENEFITS = (
    "\nBenefits. We offer medical, dental and vision insurance, a 401(k) with company "
    "match, 20 days of paid vacation per year, and 12 paid holidays. Our company has "
    "been serving customers for over 30 years.\n"
    "Equal Opportunity. We are an equal opportunity employer and consider all qualified "
    "applicants without regard to race, color, religion, sex, national origin, age, "
    "disability, citizenship, veteran status or gender identity.\n"
    "Copyright 2027 Example Corp. All rights reserved.\n"
)


def jd(core: str) -> str:
    """A realistic posting body wrapping the sentence under test."""
    return PREAMBLE + core + BENEFITS


# ------------------------------------------------------- must block: clearance and visa

BLOCK_ELIGIBILITY = [
    ("quantcast",
     "You must be work-authorized in the United States without the need for current or "
     "future employer sponsorship. We are not currently able to engage candidates on OPT."),
    ("pnc",
     "PNC will not provide sponsorship for employment visas or participate in STEM OPT "
     "for this position."),
    ("texas-sports-academy",
     "Candidates requiring OPT, CPT or STEM OPT extensions are not eligible for this "
     "role."),
    ("truveta",
     "Please note that all applicants must be authorized to work in the United States "
     "for any employer as we are unable to sponsor work visas or permits (e.g., F‑1 "
     "OPT, H‑1B) at this time."),
    ("epic",
     "Requirements: Bachelor's degree or greater. Eligible to work in the U.S. without "
     "visa sponsorship now or in the future. Relocation to Madison."),
    ("penn-state-arl",
     "ARL operates in a secure information environment. You must be a U.S. citizen to "
     "apply."),
    ("bae",
     "Please note that pursuant to a government contract, this specific position "
     "requires US Person status. Ability to obtain and maintain a security clearance."),
    ("ensco",
     "This position requires the candidate to possess a minimum of Top-Secret clearance "
     "with ability to obtain SCI. U.S. Citizenship Required: Yes."),
    ("awetomaton", "An active SECRET security clearance is required for this position."),
    ("hadrian-itar",
     "ITAR Requirements. To conform to U.S. Government space technology export "
     "regulations, including the International Traffic in Arms Regulations (ITAR), you "
     "must be a U.S. citizen or lawful permanent resident."),
    ("stackav",
     "This position may be contingent upon verifying a candidate's residence, U.S. "
     "person status, and citizenship status, as this role involves technologies subject "
     "to U.S. export control regulations."),
    # Energy Solutions, Greenhouse req 5201891007, first published 2026-08-26. The
    # sentence never says "sponsorship", so every gate keyed on that word missed it and
    # the req reached FRESH.md at tier B once per posted city — six rows on one morning.
    # Duration is the tell: OPT plus a STEM extension is authorization with an end date,
    # so "indefinitely" excludes him exactly as "no sponsorship, ever" would.
    ("energy-solutions-indefinitely",
     "Background Check Information. Information will be requested to perform the "
     "compulsory background check. A drug screen and authorization to work in the U.S. "
     "indefinitely are preconditions of employment."),
    ("permanent-basis",
     "Applicants must be authorized to work in the United States on a permanent basis."),
    # Addepar, Greenhouse req 8733680002, 2026-08-25. Read as ELIGIBLE until 2026-08-26
    # because the tense qualifier sits BEFORE the noun ("current or future visa
    # sponsorship") and the gate only matched it after. The posting names F-1/OPT as an
    # excluded category outright, so this was the worst possible false negative: the
    # pipeline was one role away from applying to it.
    ("addepar-current-or-future",
     "Applicants must be legally authorized to work in the United States for any "
     "employer without requiring current or future visa sponsorship (for example, "
     "employment-based visas such as H-1B , F-1 /OPT, or similar), and must be "
     "authorized to begin work in the U.S. promptly."),
]


@pytest.mark.parametrize(("slug", "core"), BLOCK_ELIGIBILITY, ids=[c[0] for c in BLOCK_ELIGIBILITY])
def test_blocks_ineligible(slug: str, core: str) -> None:
    v = assess("Software Engineer", "United States", jd(core))
    assert v.blocked, f"{slug}: expected blocked, got {v.decision} ({v.reasons})"


# ------------------------------------------------------------ must block: cohort mismatch

BLOCK_COHORT = [
    ("pariveda",
     "This program is intended for students graduating between December 2026 and August "
     "2027."),
    ("belvedere", "We are hiring for our Software Engineer Entry Level 2027 class."),
    ("freeform",
     "Software Engineer, New Grad. Candidates must be graduating in December 2026."),
    ("dv-trading",
     "Expected to graduate with a degree in Computer Science by Summer 2027."),
    ("old-mission", "Our 2027 graduate program starts the summer after graduation."),
    ("intern-style",
     "You must be currently enrolled in a degree program throughout the placement."),
]


@pytest.mark.parametrize(("slug", "core"), BLOCK_COHORT, ids=[c[0] for c in BLOCK_COHORT])
def test_blocks_cohort_mismatch(slug: str, core: str) -> None:
    v = assess("Software Engineer, New Grad", "New York, NY", jd(core))
    assert v.blocked, f"{slug}: expected blocked, got {v.decision} ({v.reasons})"
    assert v.cohort, f"{slug}: cohort window should be recorded"


# ----------------------------------------------------------- must block: years and lane

BLOCK_YEARS = [
    ("usbank-se2", "Software Engineer 2",
     "Bachelor's degree, or equivalent work experience. Three to five years of relevant "
     "experience."),
    ("usaa", "Software Developer",
     "Requires 4 years of software development experience in an agile environment."),
    ("samba-tv", "Fullstack Software Engineer",
     "We're looking for a Senior Full Stack Engineer. Typically 6+ years of professional "
     "full stack development, including technical leadership on a team."),
    ("anthropic-desktop", "Software Engineer, Desktop",
     "Have 5+ years of experience as a software engineer, with strong emphasis on "
     "desktop application development."),
    ("cpi-security", "Full Stack Software Engineer",
     "6+ years with our core technologies: C#, ASP.NET MVC, Web API, .NET Core."),
    ("gusto-ml", "Software Engineer, ML Platform",
     "At least 5+ years of software engineering experience in Python, Ruby or Java."),
    ("drw", "Systems Software Engineer",
     "3+ years of professional systems software engineering experience and a genuine "
     "willingness to make C++ your primary language."),
    ("patientpoint", "Software Engineer in Test",
     "4+ years of total professional experience. Hands-on experience with Android mobile "
     "test automation using Appium and Java."),
]


@pytest.mark.parametrize(("slug", "title", "core"), BLOCK_YEARS, ids=[c[0] for c in BLOCK_YEARS])
def test_blocks_years_or_lane(slug: str, title: str, core: str) -> None:
    v = assess(title, "San Francisco, CA", jd(core))
    assert v.blocked, f"{slug}: expected blocked, got {v.decision} ({v.reasons})"


BLOCK_LANE = [
    ("knapp", "Software Systems Engineer I",
     "Requires C++ or Java development experience and embedded systems work on "
     "microcontroller platforms. 60-80% travel to customer sites."),
    ("paccar", "Vehicle Software and Diagnostics Tool Test Engineer",
     "Familiarity with Controller Area Network (CAN) protocols, especially J1939, and "
     "the ability to interpret electrical schematics."),
    ("zoox", "Embedded Software Engineer - Body Systems",
     "You will write embedded C for microcontroller platforms and debug firmware on "
     "bare-metal targets."),
    ("pickle-robot", "Robotics Software Engineer",
     "Build robotics perception and motion planning for autonomous mobile robots using "
     "point cloud data from LiDAR sensors."),
    ("captech", "SaaS Platform Engineer",
     "Serve as a subject matter expert for the Microsoft ecosystem: M365, Entra ID, "
     "Intune. Strong PowerShell scripting. Align change management with ITIL."),
    ("cummins-snow", "Software Engineer",
     "Develop, configure and support solutions on the ServiceNow platform, including "
     "Flow Designer flows, business rules and UI policies. Low-code delivery."),
]


@pytest.mark.parametrize(("slug", "title", "core"), BLOCK_LANE, ids=[c[0] for c in BLOCK_LANE])
def test_blocks_off_lane(slug: str, title: str, core: str) -> None:
    v = assess(title, "United States", jd(core))
    assert v.blocked, f"{slug}: expected blocked, got {v.decision} ({v.reasons})"


# =============================== MUST PASS: the expensive failures ======================

PASS_CASES = [
    ("lyft", "Software Engineer", "San Francisco, CA",
     "As an associate software engineer, you will design, build and launch the services "
     "that power the platform's core products. B.S., M.S., or Ph.D. in Computer Science "
     "or other quantitative fields or related work experience. Proficiency in Python, "
     "Golang, or other programming language."),
    # The window INCLUDES December 2025, so the cohort gate must not fire.
    ("interface-ai", "Software Engineer", "San Francisco, CA",
     "Graduating 2024 - December 2026, or up to ~2 years of experience. You own the "
     "evals for what you build."),
    # Python satisfies the clause and Kotlin is taught, so the language gate must not fire.
    ("trovy", "Software Engineer", "New York, NY",
     "This is an entry-level role designed for someone early in their career. You're "
     "comfortable with at least one backend language (Kotlin, Java, Python, Go, "
     "TypeScript. We use Kotlin, but we'll teach you.)"),
    # "zero experience" must not read as a years floor.
    ("abbvie", "Associate Software Engineer I", "North Chicago, IL",
     "Required: Bachelor's Degree with zero experience. Experience with one or more "
     "programming languages (e.g., Python, Java, .Net)."),
    ("reliance", "AI Software Engineer I", "Chicago, IL",
     "Bachelor's degree in Computer Science required. Basic understanding of AI large "
     "language models and modern AI tooling. Familiarity with one or more programming "
     "languages such as Python, Java, JavaScript, or C++."),
    ("canonical", "Junior Software Engineer", "Home based - Worldwide",
     "We are looking for recent graduates who are excited about open source. You will "
     "complete a written interview as the first stage of our process."),
    ("nova-talent", "Software Engineer - Early Stage", "New York, NY",
     "0 to 3 years of engineering experience. We care about trajectory more than "
     "tenure. Bachelor's degree in Computer Science or a related field."),
    ("openai-learning", "Full-Stack Engineer", "San Francisco, CA",
     "Have experience working with modern web technologies such as React, TypeScript, "
     "and backend services in Python, Node.js, or similar."),
    ("agate", "Software Developer I", "Glendale, AZ",
     "Proficiency in SQL including joins, stored procedures and basic optimization. "
     "Working knowledge of HTML, CSS and JavaScript. Preferred, not required: exposure "
     "to VB.NET or C#.NET."),
    ("ekho", "Full-stack Engineer", "New York, NY",
     "You're a strong engineer across the full stack: comfortable in React on a Tuesday "
     "and deep in backend architecture on a Wednesday."),
]


@pytest.mark.parametrize(("slug", "title", "loc", "core"), PASS_CASES,
                         ids=[c[0] for c in PASS_CASES])
def test_must_not_block_viable(slug: str, title: str, loc: str, core: str) -> None:
    v = assess(title, loc, jd(core))
    assert not v.blocked, (
        f"FALSE NEGATIVE on {slug}: {v.decision} because {v.reasons}\n"
        f"quotes: {v.quotes}")


# --------------------------- false-negative traps: boilerplate must not block -----------

TRAPS = [
    ("eeo-citizenship-only",
     "We consider all applicants without regard to race, religion, citizenship or "
     "veteran status."),
    ("clearance-hedged",
     "Some client engagements may require the ability to obtain a security clearance."),
    ("company-age",
     "For over 20 years, we have served customers across North America."),
    ("legal-age", "Basic qualifications: 18+ years of age. Experience with Python."),
    ("benefits-years", "Enjoy 15 days of paid leave and a 401(k) after one year of service."),
    ("copyright-year", "Copyright 2027 Example Corp. All rights reserved."),
    ("fiscal-year", "This role supports our FY2027 platform roadmap."),
    ("scholarship-year",
     "We sponsor a scholarship for students graduating in 2027 through our foundation."),
    ("language-one-of",
     "Experience with one or more of Java, Python, Go or Ruby."),
    ("language-taught",
     "Proficiency in Kotlin is helpful but we will teach you our stack."),
    ("no-sponsor-footer",
     "Visa sponsorship is not available for this role."),
]


@pytest.mark.parametrize(("slug", "core"), TRAPS, ids=[c[0] for c in TRAPS])
def test_boilerplate_does_not_block(slug: str, core: str) -> None:
    v = assess("Software Engineer I", "Austin, TX", jd(core))
    assert not v.blocked, (
        f"FALSE NEGATIVE on boilerplate {slug}: blocked because {v.reasons}\n"
        f"quotes: {v.quotes}")


# ------------------------------------------------- runway is not the same as blocked ----

RUNWAY = [
    ("four-hands", "Visa sponsorship is not available for this role."),
    ("fidelity", "Fidelity will not provide immigration sponsorship for this position."),
    ("generic", "We are unable to sponsor or take over sponsorship of an employment visa."),
]


@pytest.mark.parametrize(("slug", "core"), RUNWAY, ids=[c[0] for c in RUNWAY])
def test_no_sponsor_statement_is_runway_not_blocked(slug: str, core: str) -> None:
    v = assess("Software Engineer I", "Austin, TX", jd(core))
    assert v.decision == "runway", f"{slug}: expected runway, got {v.decision} ({v.reasons})"


def test_quotes_always_present() -> None:
    """He should see the two sentences, or an explicit 'not stated', on every row."""
    v = assess("Software Engineer", "Remote", jd("Build things with Python."))
    assert v.quotes["work authorisation"] == "not stated"
    assert v.quotes["graduation window"] == "not stated"


def test_word_form_two_to_three_years_is_reach() -> None:
    """U.S. Bank titled this req "Software Engineer 1" and asked for "Two to three years".
    The word form must be read (a digits-only gate called it entry-level), and the floor is
    the minimum mentioned, so the verdict is reach rather than a block."""
    v = assess("Software Engineer 1", "Irving, TX",
               jd("Basic Qualifications. Bachelor's degree, or equivalent work "
                  "experience. Two to three years of relevant experience."))
    assert v.years_floor == 2, f"expected floor 2, got {v.years_floor}"
    assert v.decision == "reach", f"expected reach, got {v.decision}"


def test_snowflake_is_reach_not_block() -> None:
    """2 years plus mandatory Java is a reach, not a bar. The gate must not overreach:
    an employer who teaches its stack is exactly where a new grad gets hired."""
    v = assess("Software Engineer - Openflow", "Menlo Park, CA",
               jd("2+ years of industry experience building and operating backend or "
                  "platform services. Proficiency in Java or a similar object-oriented "
                  "language."))
    assert v.decision == "reach", f"expected reach, got {v.decision} ({v.reasons})"


def test_reach_for_two_years() -> None:
    v = assess("Software Engineer", "Remote",
               jd("Minimum of 2+ years of professional software engineering experience."))
    assert v.decision == "reach" and v.years_floor == 2

"""How much a source can be trusted, from what it is (guideline, meta-analysis, RCT,
…), how recent it is and how often it is cited. Used to rank sources and shown next to
every fact in the dossier."""
import datetime
import re

# level: 1 = strongest
LEVELS = {
    1: "Guideline / consensus statement",
    2: "Systematic review / meta-analysis",
    3: "Randomized controlled trial",
    4: "Observational study (cohort, case-control, cross-sectional)",
    5: "Narrative review / health-authority page",
    6: "Other (case report, editorial, news)",
}

_PATTERNS = [
    (1, r"guideline|consensus|position statement|scientific statement|recommendation|practice parameter"),
    (2, r"meta-analys|systematic review|umbrella review|cochrane"),
    (3, r"randomi[sz]ed controlled|randomi[sz]ed trial|clinical trial, phase iii|\brct\b"),
    (4, r"cohort|case-control|cross-sectional|observational|registry|prospective|retrospective|population-based"),
    (5, r"\breview\b"),
    (6, r"case report|editorial|letter|comment|news"),
]

# Health authorities whose pages are curated by experts (their guidance pages count as level 1-5).
AUTHORITY_DOMAINS = {
    "who.int": 1, "nice.org.uk": 1, "cdc.gov": 1, "escardio.org": 1, "heart.org": 1, "acc.org": 1,
    "diabetes.org": 1, "uspreventiveservicestaskforce.org": 1, "cochranelibrary.com": 2,
    "nih.gov": 5, "medlineplus.gov": 5, "nhs.uk": 5, "mayoclinic.org": 5, "clevelandclinic.org": 5,
    "hopkinsmedicine.org": 5, "health.harvard.edu": 5, "fda.gov": 5, "ema.europa.eu": 5,
}


def level_of(pub_types=(), title: str = "", url: str = "") -> int:
    domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url or "").split("/")[0].lower())
    for d, lvl in AUTHORITY_DOMAINS.items():
        if domain == d or domain.endswith("." + d):
            # An authority's guideline page is level 1; its other pages level 5.
            text = " ".join(pub_types) + " " + (title or "")
            return 1 if lvl == 1 or re.search(_PATTERNS[0][1], text, re.I) else lvl
    text = " ".join(pub_types or ()) + " " + (title or "")
    for lvl, pattern in _PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return lvl
    return 4 if pub_types else 6


def score(source: dict, prefer_years: int = 10) -> float:
    """Higher is better: evidence level first, then recency, then citations."""
    level = source.get("level", 6)
    year = source.get("year") or 0
    now = datetime.date.today().year
    recency = max(0.0, 1 - max(0, now - year) / max(prefer_years, 1)) if year else 0.3
    cites = min(source.get("cited_by", 0) or 0, 500) / 500
    return (7 - level) * 10 + recency * 6 + cites * 2 + (1 if source.get("text") else -5)

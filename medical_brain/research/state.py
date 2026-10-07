"""What the research workflow carries from step to step (and saves between review rounds).

outline   [{id, title, questions: [...], queries: {"literature": [...], "public": [...]},
            pending: {"literature": [...], "public": [...]}, source_ids: [...]}]
sources   {"S1": {key, origin, title, url, journal, year, pub_types, level, text, sections}}
facts     [{id, section, kind, claim, quote, source_ids, numbers, level, status, note}]
          kind: fact | myth | advice | red_flag | debated ; status: new | verified | fix
"""
from typing import TypedDict


class ResearchState(TypedDict, total=False):
    topic: str
    workflow: str                # research | research-video
    session: str
    audience: str
    user_notes: str
    angle: str
    must_cover: list
    outline: list
    sources: dict
    facts: list
    rejected: list
    focus: list                  # section ids this round works on ([] = all)
    work: list                   # sections researched in this cycle (extract + verify)
    to_extract: list             # sections the extractor runs on next
    extractor_notes: dict        # section id -> creator's request from the review
    need_sources: bool           # routing flags set by the steps
    need_fix: bool
    need_research: bool
    queries_done: list
    next_source: int
    next_fact: int
    screen_round: int
    verify_round: int
    coverage_round: int
    coverage_pass: bool
    coverage_report: str
    coverage_scores: dict
    screen_notes: str
    verify_notes: dict           # section id -> what the verifier asked to fix
    summary: str
    section_notes: dict
    review: dict                 # parsed review.md for this cycle
    review_round: int
    history: list
    settings: dict
    source_log: list


DEFAULT_SETTINGS = {
    "max_screen_rounds": 2,       # extra source searches for gaps
    "max_verify_rounds": 2,       # extractor ↔ verifier fix rounds
    "max_coverage_rounds": 2,     # coverage critic → more research
    "sources_per_query": 6,
    "sources_per_section": 8,
    "research_years": 10,
}


def new_state(topic: str, audience: str = "", notes: str = "", settings: dict = None) -> ResearchState:
    return ResearchState(
        topic=topic.strip(), audience=audience, user_notes=notes, angle="", must_cover=[], outline=[],
        sources={}, facts=[], rejected=[], focus=[], work=[], to_extract=[], extractor_notes={},
        need_sources=False, need_fix=False, need_research=False, queries_done=[], next_source=1, next_fact=1,
        screen_round=0, verify_round=0, coverage_round=0, coverage_pass=False, coverage_report="",
        coverage_scores={}, screen_notes="", verify_notes={}, summary="", section_notes={}, review={},
        review_round=0, history=[], settings={**DEFAULT_SETTINGS, **(settings or {})}, source_log=[],
    )

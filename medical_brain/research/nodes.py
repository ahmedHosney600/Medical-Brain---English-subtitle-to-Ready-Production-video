"""The research steps: plan → collect sources → screen them → extract facts → verify
them → check coverage → write the dossier. A review round (review.md) re-runs the
same steps for the sections the user asked to add or change.

System prompts live in prompts/research/<step>.md."""
import re

from ..llm import call_llm_json
from ..prompts import load_prompt
from ..utils.llm_json import UnreadableAnswer, pick
from . import sources as src
from .sources import evidence

KINDS = ("fact", "myth", "advice", "red_flag", "debated")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _section_ids(n: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[n] if n < 26 else letters[n // 26 - 1] + letters[n % 26]


def _new_section(state: dict, idx: int, raw: dict) -> dict:
    lit = [q for q in (pick(raw, "literature_queries", default=[]) or []) if str(q).strip()]
    pub = [q for q in (pick(raw, "public_queries", default=[]) or []) if str(q).strip()]
    return {"id": _section_ids(idx), "title": str(pick(raw, "title", default="Section")).strip(),
            "questions": [str(q) for q in (pick(raw, "questions", default=[]) or [])],
            "queries": {"literature": lit, "public": pub},
            "pending": {"literature": list(lit), "public": list(pub)},
            "candidates": [], "source_ids": []}


def _sections(state: dict, ids) -> list:
    wanted = set(ids or [])
    return [s for s in state.get("outline", []) if not wanted or s["id"] in wanted]


def _source_block(state: dict, sid: str, limit: int = 3500) -> str:
    s = state["sources"][sid]
    head = f"[{sid}] {s['title']} — {s.get('journal') or s.get('origin')}, {s.get('year') or 'n.d.'} — " \
           f"{evidence.LEVELS.get(s.get('level', 6), '')}"
    return head + "\n" + (s.get("text") or "")[:limit]


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[‐-―−]", "-", t)
    t = re.sub(r"[^\w%.\-' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _numbers(text: str) -> set:
    # A digit run glued to a preceding letter is part of a name (CoQ10, B12, HbA1c,
    # SGLT2), not a statistic, so it is ignored. The digit in the lookbehind keeps the
    # engine from restarting mid-run (otherwise "CoQ10" would still yield "0").
    # "COVID-19" keeps "19" (preceded by "-").
    return {n.replace(",", "") for n in re.findall(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?", text or "")}


def quote_problem(fact: dict, state: dict) -> str:
    """Why a fact can't be traced to its source ('' if it can). The quote must be in
    a cited source's text, and every number in the claim must be in the quote."""
    ids = [i for i in fact.get("source_ids", []) if i in state.get("sources", {})]
    if not ids:
        return "no valid source id"
    quote = _norm(fact.get("quote", ""))
    if len(quote) < 15:
        return "supporting quote missing or too short"
    pieces = [_norm(p) for p in re.split(r"\.\.\.|…", fact.get("quote", ""))]
    pieces = [p for p in pieces if len(p) >= 12] or [quote]
    found = False
    for sid in ids:
        text = _norm(state["sources"][sid].get("text", ""))
        if all(p in text for p in pieces):
            found = True
            break
    if not found:
        return "quote not found word-for-word in the cited source"
    missing = _numbers(fact.get("claim", "")) - _numbers(fact.get("quote", ""))
    if missing:
        return f"number(s) {sorted(missing)} in the claim are not in the quote"
    return ""


def _log(msg: str) -> None:
    print(f"  🔎 {msg}", flush=True)


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------
def topic_planner(state: dict) -> dict:
    system_prompt = load_prompt("research/topic_planner")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
AUDIENCE: {state.get("audience") or "Egyptian general public watching a medical YouTube channel (Dr. Ahmed Hosney)"}
CREATOR'S NOTES: {state.get("user_notes") or "(none)"}

Plan the research. Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.4, max_tokens=5000,
                                required=["sections"])
    except UnreadableAnswer:
        data = {}
    raw_sections = [s for s in (pick(data, "sections", default=[]) or []) if isinstance(s, dict)]
    if not raw_sections:      # never leave the research without a plan
        t = state.get("topic", "")
        raw_sections = [{"title": t, "questions": [f"What does current evidence say about {t}?"],
                         "literature_queries": [t], "public_queries": [t]}]
    outline = [_new_section(state, i, s) for i, s in enumerate(raw_sections)]
    _log(f"outline: {len(outline)} sections — " + "; ".join(s["title"] for s in outline))
    ids = [s["id"] for s in outline]
    return {"outline": outline, "angle": str(pick(data, "angle", default="")),
            "must_cover": [str(m) for m in (pick(data, "must_cover", default=[]) or [])],
            "focus": ids, "work": ids, "screen_round": 0, "verify_round": 0, "coverage_round": 0}


def source_collector(state: dict) -> dict:
    settings = state.get("settings", {})
    per_query, years = settings.get("sources_per_query", 6), settings.get("research_years", 10)
    outline = [dict(s) for s in state.get("outline", [])]
    sources = {k: dict(v) for k, v in state.get("sources", {}).items()}
    by_key = {v["key"]: k for k, v in sources.items()}
    next_id = state.get("next_source", 1)
    done = list(state.get("queries_done", []))
    focus = set(state.get("focus") or [s["id"] for s in outline])
    for sec in outline:
        if sec["id"] not in focus:
            continue
        found = []
        for kind in ("literature", "public"):
            for q in sec.get("pending", {}).get(kind, []):
                tag = f"{kind}:{q}"
                if tag in done:
                    continue
                done.append(tag)
                found.extend(src.run_query(kind, q, per_query, years))
        new_here = 0
        for s in found:
            if not s.get("text"):
                continue
            sid = by_key.get(s["key"])
            if not sid:
                sid = f"S{next_id}"
                next_id += 1
                s["text"] = s["text"][:6000]
                s["sections"] = []
                sources[sid] = s
                by_key[s["key"]] = sid
                new_here += 1
            if sec["id"] not in sources[sid]["sections"]:
                sources[sid]["sections"].append(sec["id"])
            if sid not in sec["candidates"]:
                sec["candidates"].append(sid)
        sec["candidates"].sort(key=lambda i: -evidence.score(sources[i], years))
        sec["candidates"] = sec["candidates"][:settings.get("sources_per_section", 8) * 2]
        sec["pending"] = {"literature": [], "public": []}
        _log(f"{sec['id']} {sec['title'][:50]}: {new_here} new sources, {len(sec['candidates'])} candidates")
    return {"outline": outline, "sources": sources, "next_source": next_id, "queries_done": done,
            "source_log": list(src.http.log)}


def source_screener(state: dict) -> dict:
    settings = state.get("settings", {})
    outline = [dict(s) for s in state.get("outline", [])]
    focus = set(state.get("focus") or [s["id"] for s in outline])
    blocks = []
    for sec in outline:
        if sec["id"] not in focus:
            continue
        lines = [f"## SECTION {sec['id']}: {sec['title']}", "Questions: " + " | ".join(sec["questions"])]
        for sid in sec["candidates"]:
            lines.append(_source_block(state, sid, limit=700))
        blocks.append("\n".join(lines))
    if not blocks:
        return {"need_sources": False, "to_extract": state.get("work", [])}
    system_prompt = load_prompt("research/source_screener")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
KEEP UP TO {settings.get("sources_per_section", 8)} SOURCES PER SECTION.

{chr(10).join(blocks)}

Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=5000,
                                required=["sections"])
    except UnreadableAnswer:
        data = {}
    verdicts = {str(pick(v, "id", default="")): v for v in (pick(data, "sections", default=[]) or [])
                if isinstance(v, dict)}
    gaps = []
    for sec in outline:
        if sec["id"] not in focus:
            continue
        v = verdicts.get(sec["id"], {})
        keep = [i for i in (pick(v, "keep", default=[]) or []) if i in sec["candidates"]]
        if not keep:                       # no verdict: keep the best-ranked sources
            keep = sec["candidates"][:settings.get("sources_per_section", 8)]
        # The model's pick first, then earlier keeps; capped so the section's source
        # list cannot grow without bound each screening round.
        sec["source_ids"] = list(dict.fromkeys(keep + sec.get("source_ids", [])))[
            :settings.get("max_sources_per_section", 12)]
        lit = [q for q in (pick(v, "literature_queries", default=[]) or []) if q]
        pub = [q for q in (pick(v, "public_queries", default=[]) or []) if q]
        has_queries = bool(lit or pub)
        # Only search again for a section with a real, unfilled gap (or almost no
        # sources at all): a reported gap when the section is already stocked is not
        # worth a full extra search round.
        need_more = (bool(pick(v, "gaps", default=[]) or [])
                     and len(sec["source_ids"]) < settings.get("sources_per_section", 8))
        if has_queries and (need_more or len(sec["source_ids"]) < 2):
            sec["pending"] = {"literature": lit, "public": pub}
            gaps.append(sec["id"])
    rounds = state.get("screen_round", 0)
    more = bool(gaps) and rounds < settings.get("max_screen_rounds", 2)
    _log(f"screening: kept {sum(len(s['source_ids']) for s in outline if s['id'] in focus)} sources"
         + (f"; searching again for gaps in {', '.join(gaps)}" if more else ""))
    return {"outline": outline, "screen_round": rounds + 1, "need_sources": more,
            "focus": gaps if more else state.get("focus", []),
            "to_extract": state.get("work", []), "screen_notes": str(pick(data, "notes", default=""))}


FIX_MODE = ("FIX MODE: return ONLY corrected versions of the facts listed under FIX THESE FACTS "
            "(same meaning, corrected claim and/or exact quote). Do not add any other facts. "
            "Drop a fact if the sources do not support it.")


def fact_extractor(state: dict) -> dict:
    settings = state.get("settings", {})
    system_prompt = load_prompt("research/fact_extractor")
    facts = [f for f in state.get("facts", []) if f.get("status") != "fix"]
    # Facts sent back for fixing are replaced by this extraction; if they don't come
    # back corrected they stay visible in the dossier's "not included" list.
    rejected = list(state.get("rejected", [])) + [
        dict(f, status="rejected", note=f"{f.get('note', '')} (sent back for correction)")
        for f in state.get("facts", []) if f.get("status") == "fix"]
    redo = {}
    for f in state.get("facts", []):
        if f.get("status") == "fix":
            redo.setdefault(f["section"], []).append(f)
    next_fact = state.get("next_fact", 1)
    notes = state.get("verify_notes", {}) or {}
    user_notes = state.get("extractor_notes", {}) or {}
    budget = settings.get("max_facts_per_section", 20)
    outline = [dict(s) for s in state.get("outline", [])]
    for sec in _sections({"outline": outline}, state.get("to_extract")):
        fixing = redo.get(sec["id"]) or []
        extracted = list(sec.get("extracted", []))
        if fixing:
            # FIX MODE: only the flagged facts and the sources they cite. A normal
            # new-source pass here is what inflated the facts on every fix round.
            send_ids = list(dict.fromkeys(i for f in fixing for i in f.get("source_ids", [])
                                          if i in state.get("sources", {})))
        else:
            send_ids = [i for i in sec.get("source_ids", []) if i not in extracted]
        if not send_ids:
            _log(f"{sec['id']} {sec['title'][:50]}: nothing new")
            continue
        have = [f for f in facts if f["section"] == sec["id"] and f["status"] == "verified"]
        room = budget - len(have)
        if not fixing:
            if room <= 0:
                _log(f"{sec['id']} {sec['title'][:50]}: fact budget reached")
                continue
            fix_part = f"\n\nEXTRACT AT MOST {room} NEW FACTS — the most valuable ones."
        else:
            fix_part = ("\n\n" + FIX_MODE + "\nFIX THESE FACTS:\n" + "\n".join(
                f"- {f['claim']}\n  problem: {f.get('note', '')}\n  quote: \"{f.get('quote', '')}\""
                for f in fixing))
        user_prompt = f"""TOPIC: {state.get("topic", "")}
SECTION {sec['id']}: {sec['title']}
QUESTIONS TO ANSWER: {" | ".join(sec["questions"])}
{("CREATOR'S REQUEST FOR THIS SECTION: " + user_notes[sec['id']]) if user_notes.get(sec['id']) else ""}
{("VERIFIER NOTES: " + notes[sec['id']]) if notes.get(sec['id']) else ""}
ALREADY VERIFIED (don't repeat): {" | ".join(f["claim"] for f in have) or "(none)"}{fix_part}

SOURCES (quote ONLY from these texts):
{chr(10).join(_source_block(state, sid) for sid in send_ids)}

Output ONLY the JSON object."""
        try:
            data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=9000,
                                    required=["facts"])
        except UnreadableAnswer:
            data = {}
        if not fixing and data:
            # These sources are done; a later pass must not send them again (an
            # unreadable answer leaves them for the next pass).
            sec["extracted"] = list(dict.fromkeys(extracted + send_ids))
        added = 0
        seen = {_norm(f["claim"]) for f in facts}
        limit = len(fixing) if fixing else room          # fix mode: corrections only
        for raw in pick(data, "facts", default=[]) or []:
            if not isinstance(raw, dict) or not str(pick(raw, "claim", default="")).strip():
                continue
            if _norm(str(pick(raw, "claim"))) in seen:
                continue                       # already have it
            seen.add(_norm(str(pick(raw, "claim"))))
            ids = [str(i).strip("[] ") for i in (pick(raw, "source_ids", default=[]) or [])]
            kind = str(pick(raw, "kind", default="fact")).lower().replace(" ", "_")
            levels = [state["sources"][i].get("level", 6) for i in ids if i in state["sources"]]
            facts.append({
                "id": f"F{next_fact}", "section": sec["id"], "kind": kind if kind in KINDS else "fact",
                "claim": str(pick(raw, "claim")).strip(), "quote": str(pick(raw, "quote", default="")).strip(),
                "source_ids": ids, "level": min(levels) if levels else 6,
                "question": str(pick(raw, "question", default="")), "status": "new", "note": ""})
            next_fact += 1
            added += 1
            if added >= limit:
                break
        _log(f"{sec['id']} {sec['title'][:50]}: {added} facts extracted")
    # A corrected fact that made it back is no longer "not included".
    kept = {_norm(f["claim"]) for f in facts}
    rejected = [r for r in rejected if not (r.get("note", "").endswith("(sent back for correction)")
                                            and _norm(r["claim"]) in kept)]
    return {"facts": facts, "next_fact": next_fact, "rejected": rejected, "outline": outline}


def fact_verifier(state: dict) -> dict:
    settings = state.get("settings", {})
    facts = [dict(f) for f in state.get("facts", [])]
    rejected = list(state.get("rejected", []))
    by_section = {}
    # 1. Code check: every new fact must be traceable to its source, word for word.
    for f in facts:
        if f["status"] != "new":
            continue
        why = quote_problem(f, state)
        if why:
            f["status"], f["note"] = "fix", why
        else:
            by_section.setdefault(f["section"], []).append(f)
    # 2. Model check: is each claim faithful to its quote, current, and consistent?
    system_prompt = load_prompt("research/fact_verifier")
    for sid, group in by_section.items():
        sec = next((s for s in state.get("outline", []) if s["id"] == sid), {"title": sid})
        others = [f"{f['id']}: {f['claim']}" for f in facts if f["status"] == "verified"][:60]
        items = "\n\n".join(
            f"{f['id']} ({f['kind']}) CLAIM: {f['claim']}\nQUOTE: \"{f['quote']}\"\nSOURCE: "
            + "; ".join(f"[{i}] {state['sources'][i]['title']} ({state['sources'][i].get('year') or 'n.d.'}, "
                        f"{evidence.LEVELS.get(state['sources'][i].get('level', 6), '')})"
                        for i in f["source_ids"] if i in state["sources"])
            for f in group)
        user_prompt = f"""TOPIC: {state.get("topic", "")}
SECTION {sid}: {sec.get('title', '')}

FACTS TO VERIFY:
{items}

OTHER VERIFIED FACTS (check for contradictions):
{chr(10).join(others) or "(none)"}

Output ONLY the JSON object."""
        try:
            data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.1, max_tokens=7000,
                                    required=["verdicts"])
        except UnreadableAnswer:
            data = {}
        verdicts = {str(pick(v, "id", default="")): v for v in (pick(data, "verdicts", default=[]) or [])
                    if isinstance(v, dict)}
        for f in group:
            v = verdicts.get(f["id"])
            if v is None:
                f["status"], f["note"] = "fix", "the verifier gave no verdict"
                continue
            verdict = str(pick(v, "verdict", default="")).lower()
            reason = str(pick(v, "reason", default=""))
            corrected = str(pick(v, "corrected_claim", default="") or "").strip()
            if verdict.startswith("verif"):
                f["status"] = "verified"
                if pick(v, "debated", kind=bool):
                    f["kind"] = "debated"
            elif verdict.startswith("fix") and corrected:
                candidate = dict(f, claim=corrected)
                if not quote_problem(candidate, state):
                    f.update(claim=corrected, status="verified", note="wording corrected by the verifier")
                else:
                    f["status"], f["note"] = "fix", reason or "needs correction"
            elif verdict.startswith("fix"):
                f["status"], f["note"] = "fix", reason or "needs correction"
            else:
                f["status"], f["note"] = "rejected", reason or "rejected by the verifier"
    keep = []
    for f in facts:
        if f["status"] == "rejected":
            rejected.append(f)
        else:
            keep.append(f)
    rounds = state.get("verify_round", 0)
    fix_sections = sorted({f["section"] for f in keep if f["status"] == "fix"})
    more = bool(fix_sections) and rounds < settings.get("max_verify_rounds", 2)
    if not more:                    # out of rounds: what still isn't right is left out
        for f in keep:
            if f["status"] == "fix":
                f["status"] = "rejected"
                f["note"] = (f.get("note") or "") + " (unresolved)"
                rejected.append(f)
        keep = [f for f in keep if f["status"] != "rejected"]
    verified = sum(1 for f in keep if f["status"] == "verified")
    to_fix = sum(1 for f in keep if f["status"] == "fix")
    _log(f"verification round {rounds + 1}: {verified} verified, {to_fix} to fix, {len(rejected)} left out so far")
    notes = {s: "; ".join(f["note"] for f in keep if f["section"] == s and f["status"] == "fix") for s in fix_sections}
    return {"facts": keep, "rejected": rejected, "verify_round": rounds + 1, "need_fix": more,
            "to_extract": fix_sections if more else [], "verify_notes": notes if more else {}}


def coverage_critic(state: dict) -> dict:
    settings = state.get("settings", {})
    lines = []
    for sec in state.get("outline", []):
        lines.append(f"## {sec['id']}: {sec['title']}\nQuestions: " + " | ".join(sec["questions"]))
        for f in state.get("facts", []):
            if f["section"] == sec["id"] and f["status"] == "verified":
                lines.append(f"- {f['id']} [{f['kind']}] {f['claim']}")
    system_prompt = load_prompt("research/coverage_critic")
    user_prompt = f"""TOPIC: {state.get("topic", "")}
ANGLE: {state.get("angle", "")}
MUST COVER: {" | ".join(state.get("must_cover", [])) or "(see topic)"}
{("CREATOR'S REVIEW NOTES: " + state["review"].get("notes", "")) if state.get("review", {}).get("notes") else ""}

VERIFIED FACTS BY SECTION:
{chr(10).join(lines)}

Output ONLY the JSON object."""
    try:
        data, raw = call_llm_json(system_prompt, user_prompt, temperature=0.2, max_tokens=5000,
                                  required=["coverage_score", "missing"])
    except UnreadableAnswer as e:
        data, raw = {}, e.raw
    scores = {k: pick(data, k + "_score", kind=int) for k in ("coverage", "accuracy", "value")}
    pass_score = settings.get("coverage_pass_score", 7)
    passed = bool(pick(data, "pass", default=False, kind=bool)) or all((v or 0) >= pass_score
                                                                        for v in scores.values())
    rounds = state.get("coverage_round", 0)
    outline = [dict(s) for s in state.get("outline", [])]
    # Plateau: once scores have stopped moving, more searching is wasted work.
    prev = state.get("coverage_scores") or {}
    improved = any((scores.get(k) or 0) - (prev.get(k) or 0) >= 1 for k in scores)
    plateau = rounds > 0 and bool(prev) and not improved
    new_work = []
    if not passed and rounds < settings.get("max_coverage_rounds", 2) and not plateau:
        # Merge everything a section is missing into ONE work item: the critic may list
        # a section more than once and a later item used to wipe the earlier queries.
        merged, order = {}, []
        for m in pick(data, "missing", default=[]) or []:
            if not isinstance(m, dict):
                continue
            lit = [q for q in (pick(m, "literature_queries", default=[]) or []) if q]
            pub = [q for q in (pick(m, "public_queries", default=[]) or []) if q]
            if not (lit or pub):
                continue
            sid = str(pick(m, "section_id", default="")).strip()
            if not sid:
                # A brand-new section has no id yet: key it by title (or a unique
                # placeholder) so separate new sections don't merge into one.
                title = str(pick(m, "title", default="")).strip().lower()
                sid = "__new:" + (title or str(len(order)))
            if sid not in merged:
                merged[sid] = {"literature": [], "public": [], "questions": [], "raw": m}
                order.append(sid)
            e = merged[sid]
            e["literature"] = list(dict.fromkeys(e["literature"] + lit))
            e["public"] = list(dict.fromkeys(e["public"] + pub))
            e["questions"] = list(dict.fromkeys(
                e["questions"] + [str(q) for q in (pick(m, "questions", default=[]) or [])]))
        cap = settings.get("max_coverage_sections", 3)
        for sid in order[:cap]:
            e = merged[sid]
            target = next((s for s in outline if s["id"] == sid), None)
            if target is None:
                target = _new_section(state, len(outline), e["raw"])
                target["questions"] = list(e["questions"])
                target["pending"] = {"literature": list(e["literature"]), "public": list(e["public"])}
                outline.append(target)
            else:
                pending = target.setdefault("pending", {"literature": [], "public": []})
                pending["literature"] = list(dict.fromkeys(list(pending.get("literature", [])) + e["literature"]))
                pending["public"] = list(dict.fromkeys(list(pending.get("public", [])) + e["public"]))
                target["questions"] = list(dict.fromkeys(target.get("questions", []) + e["questions"]))
            new_work.append(target["id"])
    more = bool(new_work)
    if more:
        why = f"; researching more for {', '.join(new_work)}"
    elif not passed and plateau:
        why = "; scores stopped improving"
    else:
        why = ""
    _log(f"coverage round {rounds + 1}: scores {scores}" + why)
    out = {"coverage_round": rounds + 1, "coverage_pass": passed, "coverage_scores": scores,
           "coverage_report": str(pick(data, "report", default="") or raw or ""), "need_research": more,
           "outline": outline}
    if more:
        out.update(focus=new_work, work=new_work, screen_round=0, verify_round=0)
    return out


def dossier_editor(state: dict) -> dict:
    """A short overview per section, written only from verified facts (every sentence
    must cite a fact id, which code checks)."""
    facts = [f for f in state.get("facts", []) if f["status"] == "verified"]
    if not facts:
        return {"summary": "", "section_notes": {}}
    lines = []
    for sec in state.get("outline", []):
        lines.append(f"## {sec['id']}: {sec['title']}")
        lines += [f"- {f['id']} [{f['kind']}] {f['claim']}" for f in facts if f["section"] == sec["id"]]
    system_prompt = load_prompt("research/dossier_editor")
    user_prompt = f"""TOPIC: {state.get("topic", "")}

VERIFIED FACTS:
{chr(10).join(lines)}

Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=4000, required=["summary"])
    except UnreadableAnswer:
        data = {}
    valid = {f["id"] for f in facts}

    def cited_only(text: str) -> str:
        # Keep only sentences that cite a real verified fact.
        sentences = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        kept = [s for s in sentences if (refs := re.findall(r"\[?(F\d+)\]?", s)) and all(r in valid for r in refs)]
        return " ".join(kept)

    notes = {str(k): cited_only(v) for k, v in (pick(data, "section_notes", default={}) or {}).items()}
    return {"summary": cited_only(pick(data, "summary", default="")), "section_notes": notes}


def review_planner(state: dict) -> dict:
    """Turns the creator's review.md into research work for the next round."""
    review = state.get("review", {}) or {}
    outline = [dict(s) for s in state.get("outline", [])]
    facts = list(state.get("facts", []))
    system_prompt = load_prompt("research/review_planner")
    user_prompt = f"""TOPIC: {state.get("topic", "")}

CURRENT OUTLINE:
{chr(10).join(f"{s['id']}: {s['title']} — " + " | ".join(s['questions']) for s in outline)}

CURRENT VERIFIED FACTS:
{chr(10).join(f"{f['id']} ({f['section']}): {f['claim']}" for f in facts if f['status'] == 'verified')}

CREATOR'S REVIEW:
ADD: {review.get("add", "") or "(nothing)"}
CHANGE: {review.get("change", "") or "(nothing)"}
REMOVE: {review.get("remove", "") or "(nothing)"}
NOTES: {review.get("notes", "") or "(nothing)"}

Output ONLY the JSON object."""
    try:
        data, _ = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=5000,
                                required=["new_sections", "edit_sections"])
    except UnreadableAnswer:
        data = {}
    work = []
    remove_sections = {str(i) for i in (pick(data, "remove_sections", default=[]) or [])}
    remove_facts = {str(i) for i in (pick(data, "remove_facts", default=[]) or [])}
    outline = [s for s in outline if s["id"] not in remove_sections]
    facts = [f for f in facts if f["id"] not in remove_facts and f["section"] not in remove_sections]
    extractor_notes = {}
    for e in pick(data, "edit_sections", default=[]) or []:
        if not isinstance(e, dict):
            continue
        sec = next((s for s in outline if s["id"] == str(pick(e, "id", default=""))), None)
        if not sec:
            continue
        sec["questions"] = sec["questions"] + [str(q) for q in (pick(e, "add_questions", default=[]) or [])]
        sec["pending"] = {"literature": [q for q in (pick(e, "literature_queries", default=[]) or []) if q],
                          "public": [q for q in (pick(e, "public_queries", default=[]) or []) if q]}
        if pick(e, "note", default=""):
            extractor_notes[sec["id"]] = str(pick(e, "note"))
        work.append(sec["id"])
    for raw in pick(data, "new_sections", default=[]) or []:
        if isinstance(raw, dict):
            sec = _new_section(state, len(outline), raw)
            # ids must stay unique even after removals
            while any(s["id"] == sec["id"] for s in outline):
                sec["id"] = sec["id"] + "2"
            outline.append(sec)
            work.append(sec["id"])
            if pick(raw, "note", default=""):
                extractor_notes[sec["id"]] = str(pick(raw, "note"))
    _log(f"review round {state.get('review_round', 0) + 1}: {len(work)} sections to research, "
         f"{len(remove_sections)} removed, {len(remove_facts)} facts removed")
    return {"outline": outline, "facts": facts, "focus": work, "work": work, "to_extract": [],
            "extractor_notes": extractor_notes, "screen_round": 0, "verify_round": 0, "coverage_round": 0,
            "review_round": state.get("review_round", 0) + 1,
            "history": state.get("history", []) + [{"round": state.get("review_round", 0) + 1, "review": review}]}

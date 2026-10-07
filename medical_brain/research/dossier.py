"""The research dossier: the verified facts, organised by the outline, every one with
its sources — written by code from the verified facts, so nothing unverified can slip
in. Also the review file the creator edits, and the source text the video workflow
is built from."""
import json
import os
import re

from .sources import evidence

KIND_TITLES = {"fact": "Key facts", "advice": "Practical guidance", "red_flag": "Red flags — when to see a doctor",
               "myth": "Myths & misconceptions (with the evidence)", "debated": "Debated / uncertain points"}
LEVEL_BADGE = {1: "🟢 Guideline", 2: "🟢 Meta-analysis", 3: "🟢 RCT", 4: "🟡 Observational",
               5: "🟡 Review / health authority", 6: "⚪ Other"}


def _cites(f: dict) -> str:
    return "".join(f"[{i}]" for i in f.get("source_ids", []))


def _link_refs(text: str, facts: dict) -> str:
    """[F12] in an editor's overview → that fact's source citations."""
    return re.sub(r"\[?(F\d+)\]?", lambda m: _cites(facts[m.group(1)]) if m.group(1) in facts else "", text or "")


def verified_facts(state: dict) -> list:
    return [f for f in state.get("facts", []) if f.get("status") == "verified"]


def used_sources(state: dict) -> list:
    ids = []
    for f in verified_facts(state):
        for i in f.get("source_ids", []):
            if i in state.get("sources", {}) and i not in ids:
                ids.append(i)
    return sorted(ids, key=lambda i: int(re.sub(r"\D", "", i) or 0))


def reference_lines(state: dict, ids=None) -> list:
    out = []
    for sid in ids if ids is not None else used_sources(state):
        s = state["sources"][sid]
        meta = ", ".join(x for x in (s.get("journal") or s.get("origin"), str(s.get("year") or "")) if x)
        out.append(f"[{sid}] {s['title']} — {meta} — {evidence.LEVELS.get(s.get('level', 6), '')} — {s['url']}")
    return out


def render_markdown(state: dict) -> str:
    facts = verified_facts(state)
    by_id = {f["id"]: f for f in facts}
    md = [f"# 🔬 RESEARCH DOSSIER — {state.get('topic', '')}", ""]
    scores = state.get("coverage_scores") or {}
    md.append(f"Review round {state.get('review_round', 0)} · {len(facts)} verified facts from "
              f"{len(used_sources(state))} sources · coverage {scores.get('coverage', '?')}/10, "
              f"accuracy {scores.get('accuracy', '?')}/10, value {scores.get('value', '?')}/10"
              + ("" if state.get("coverage_pass") else " · ⚠️ coverage check not passed (see notes)"))
    md += ["", "> Every fact below was extracted from the cited source's own text, matched word-for-word "
           "to a quote, and checked by a separate verifier model. Edit **review.md** to ask for changes.", ""]
    if state.get("angle"):
        md += [f"**Angle:** {state['angle']}", ""]
    if state.get("summary"):
        md += ["## Overview", "", _link_refs(state["summary"], by_id), ""]
    md += ["## Outline", ""] + [f"{s['id']}. {s['title']}" for s in state.get("outline", [])] + [""]
    for sec in state.get("outline", []):
        sec_facts = [f for f in facts if f["section"] == sec["id"]]
        md += [f"## {sec['id']}. {sec['title']}", ""]
        if sec.get("questions"):
            md += ["*Questions:* " + " · ".join(sec["questions"]), ""]
        note = (state.get("section_notes") or {}).get(sec["id"])
        if note:
            md += [_link_refs(note, by_id), ""]
        if not sec_facts:
            md += ["_No verified facts yet for this section._", ""]
        for kind in ("fact", "advice", "red_flag", "myth", "debated"):
            group = [f for f in sec_facts if f["kind"] == kind]
            if not group:
                continue
            md += [f"### {KIND_TITLES[kind]}", ""]
            for f in group:
                md.append(f"- **{f['id']}** {f['claim']} {_cites(f)} · {LEVEL_BADGE.get(f.get('level', 6), '')}")
                md.append(f"  > \"{f['quote']}\"")
            md.append("")
    numbers = [f for f in facts if re.search(r"\d", f["claim"])]
    if numbers:
        md += ["## Key numbers", "", "| Fact | Claim | Sources |", "|---|---|---|"]
        md += [f"| {f['id']} | {f['claim'].replace('|', '/')} | {_cites(f)} |" for f in numbers] + [""]
    rejected = state.get("rejected", [])
    if rejected:
        md += ["## Not included (could not be verified)", ""]
        md += [f"- ~~{f['claim']}~~ — {f.get('note') or 'rejected'}" for f in rejected[-40:]] + [""]
    if state.get("coverage_report"):
        md += ["## Reviewer notes on coverage", "", state["coverage_report"], ""]
    md += ["## References", ""] + [f"- {line}" for line in reference_lines(state)] + [""]
    problems = sorted({p for _, _, p in state.get("source_log", [])})
    if problems:
        md += ["## Sources that did not answer", "", "- " + "\n- ".join(problems[:15]), ""]
    return "\n".join(md)


REVIEW_TEMPLATE = """# ✍️ REVIEW — {topic}

Read dossier.md, then fill in this file and run:
    python3 main.py --continue {session}

Set APPROVED to yes when the research is complete and correct (nothing else needed).
Otherwise write what you want below — plain words in English or Arabic are fine.

APPROVED: no

ADD:
(new subtopics, questions or angles to research — one per line)

CHANGE:
(facts or sections to correct, refocus or deepen — mention the fact id, e.g. F12)

REMOVE:
(sections or facts to drop, e.g. section D, F7)

NOTES:
(anything else: audience, depth, Egyptian context, …)
"""


def write_review_file(path: str, topic: str, session: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(REVIEW_TEMPLATE.format(topic=topic, session=session))


def parse_review(text: str) -> dict:
    """review.md → {approved, add, change, remove, notes} (placeholder lines ignored)."""
    out = {"approved": False, "add": "", "change": "", "remove": "", "notes": ""}
    m = re.search(r"^\s*APPROVED\s*:\s*(\S+)", text or "", re.IGNORECASE | re.MULTILINE)
    out["approved"] = bool(m and m.group(1).strip().lower() in ("yes", "y", "true", "نعم", "ok", "approved"))
    for key in ("ADD", "CHANGE", "REMOVE", "NOTES"):
        m = re.search(rf"^\s*{key}\s*:\s*(.*?)(?=^\s*(?:ADD|CHANGE|REMOVE|NOTES|APPROVED)\s*:|\Z)",
                      text or "", re.IGNORECASE | re.MULTILINE | re.DOTALL)
        body = m.group(1) if m else ""
        lines = [l.strip() for l in body.splitlines()
                 if l.strip() and not re.match(r"^\(.*\)$", l.strip())]
        out[key.lower()] = "\n".join(lines)
    return out


def has_requests(review: dict) -> bool:
    return any(review.get(k) for k in ("add", "change", "remove", "notes"))


def save(state: dict, folder: str) -> dict:
    """Writes dossier.md, dossier.json and research_state.json. Returns their paths."""
    os.makedirs(folder, exist_ok=True)
    paths = {"md": os.path.join(folder, "dossier.md"), "json": os.path.join(folder, "dossier.json"),
             "state": os.path.join(folder, "research_state.json")}
    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write(render_markdown(state))
    dossier = {"topic": state.get("topic"), "outline": state.get("outline"), "facts": verified_facts(state),
               "sources": {i: state["sources"][i] for i in used_sources(state)},
               "rejected": state.get("rejected", []), "history": state.get("history", [])}
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)
    with open(paths["state"], "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return paths


def video_source(state: dict) -> str:
    """The dossier as the source text of the video workflow: English, in outline order,
    every statement with its source ids. The video workflow treats it like an English
    script — but here every claim is verified and cited."""
    facts = verified_facts(state)
    by_id = {f["id"]: f for f in facts}
    out = [f"RESEARCH DOSSIER (verified, cited) — TOPIC: {state.get('topic', '')}", ""]
    if state.get("angle"):
        out += [f"ANGLE: {state['angle']}", ""]
    if state.get("summary"):
        out += ["OVERVIEW: " + _link_refs(state["summary"], by_id), ""]
    for sec in state.get("outline", []):
        sec_facts = [f for f in facts if f["section"] == sec["id"]]
        if not sec_facts:
            continue
        out.append(f"SECTION {sec['id']}: {sec['title']}")
        for kind in ("fact", "advice", "red_flag", "myth", "debated"):
            for f in (x for x in sec_facts if x["kind"] == kind):
                label = {"myth": "MYTH vs EVIDENCE", "red_flag": "RED FLAG", "advice": "PRACTICAL",
                         "debated": "DEBATED"}.get(kind, "FACT")
                out.append(f"- {label}: {f['claim']} {_cites(f)}")
        out.append("")
    out += ["SOURCES:"] + reference_lines(state)
    return "\n".join(out)


def key_sources(state: dict, n: int = 6) -> list:
    """The strongest sources (best evidence, most used) for a video description."""
    use = {}
    for f in verified_facts(state):
        for i in f.get("source_ids", []):
            use[i] = use.get(i, 0) + 1
    ranked = sorted(use, key=lambda i: (state["sources"][i].get("level", 6), -use[i]))
    return reference_lines(state, [i for i in ranked if i in state.get("sources", {})][:n])

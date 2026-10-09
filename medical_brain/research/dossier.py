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
    ids = list(f.get("source_ids", []))
    for m in f.get("also_supported_by", []) or []:
        ids += list(m.get("source_ids", []))
    return "".join(f"[{i}]" for i in dict.fromkeys(ids))


def _source_names(state: dict, ids) -> str:
    """Human names (journal/site, else origin) for a list of source ids."""
    names = []
    for i in ids:
        s = state.get("sources", {}).get(i) or {}
        name = s.get("journal") or s.get("origin") or i
        if name and name not in names:
            names.append(str(name))
    return ", ".join(names)


STRENGTH_LABELS = {"proven": "✅ Proven", "probably_helps": "🟢 Probably helps",
                   "weak_or_disputed": "⚠️ Weak / disputed", "no_benefit": "❌ No benefit",
                   "harmful": "⛔ Harmful", "untested": "❔ Untested"}

# Classic-check verdicts, strongest evidence first: any harmful fact dominates.
CLASSIC_VERDICTS = (("harmful", "Risky / harmful"),
                    ("proven", "Helps (proven)"),
                    ("probably_helps", "May help (limited evidence)"),
                    ("no_benefit", "Doesn't help"),
                    ("weak_or_disputed", "Unclear / disputed"),
                    ("untested", "Not studied well"))
_EVIDENCE_OPTION_VERDICTS = {"Helps (proven)", "May help (limited evidence)"}
NO_EVIDENCE = "No evidence found in the sources searched"
SEE_FACTS = "See facts"


def _complementary(state: dict) -> dict:
    comp = state.get("complementary") or {}
    return comp if isinstance(comp, dict) else {}


def _is_complementary_item(state: dict, item: str) -> bool:
    key = str(item or "").strip().lower()
    return any(key == str(x).strip().lower() for x in (_complementary(state).get("items") or []))


def _classic_item_texts(state: dict) -> list:
    """The classic-check items in plan order, non-empty and de-duplicated."""
    out, seen = [], set()
    for c in (state.get("classic_checks") or []):
        if not isinstance(c, dict):
            continue
        item = str(c.get("item", "")).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


MIXED_EVIDENCE = "Mixed evidence (studies disagree)"


def classic_check_table(state: dict) -> list:
    """One row per classic-check item: the strongest verdict from its verified facts.

    Precedence: harmful > (benefit AND no-benefit facts -> "Mixed evidence") > proven >
    probably_helps > no_benefit > weak_or_disputed > untested; facts that all have n/a ->
    "See facts"; no facts -> "No evidence found…".
    Built only from verified facts whose ``check`` names the item."""
    facts = verified_facts(state)
    rows = []
    for item in _classic_item_texts(state):
        matched = [f for f in facts if str(f.get("check", "") or "").strip().lower() == item.lower()]
        strengths = {str(f.get("strength", "n/a") or "n/a") for f in matched}
        verdict = next((label for strength, label in CLASSIC_VERDICTS if strength in strengths), None)
        # A benefit finding next to a no-benefit finding is a conflict, not a "helps":
        # say so (and never offer it as an option) instead of picking the hopeful one.
        if "harmful" not in strengths and "no_benefit" in strengths \
                and strengths & {"proven", "probably_helps"}:
            verdict = MIXED_EVIDENCE
        if verdict is None:
            verdict = SEE_FACTS if matched else NO_EVIDENCE
        source_ids = []
        for f in matched:
            for i in f.get("source_ids", []):
                if i not in source_ids:
                    source_ids.append(i)
        rows.append({"item": item, "verdict": verdict,
                     "fact_ids": [f["id"] for f in matched],
                     "source_ids": source_ids, "sources": _source_names(state, source_ids),
                     "harmful": "harmful" in strengths,
                     "complementary": _is_complementary_item(state, item)})
    return rows


def complementary_lists(rows: list) -> tuple:
    """(evidence-based, popular-but-not-proven) complementary rows, from the table only.

    An option needs a proven / probably-helps verdict and no harmful fact; everything
    else is listed honestly under "popular but not proven"."""
    comp = [r for r in rows if r.get("complementary")]
    evidence = [r for r in comp if r.get("verdict") in _EVIDENCE_OPTION_VERDICTS and not r.get("harmful")]
    chosen = {r["item"].strip().lower() for r in evidence}
    return evidence, [r for r in comp if r["item"].strip().lower() not in chosen]


def _classic_table_markdown(rows: list) -> list:
    md = ["## Classic checks — what the evidence says", "",
          "| Item | Verdict | Facts |", "|---|---|---|"]
    for r in rows:
        cell = ", ".join(r["fact_ids"]) if r["fact_ids"] else "—"
        cites = "".join(f"[{i}]" for i in r["source_ids"])
        md.append(f"| {r['item'].replace('|', '/')} | {r['verdict']} | {cell}{(' ' + cites) if cites else ''} |")
    return md + [""]


def _complementary_markdown(rows: list) -> list:
    evidence, popular = complementary_lists(rows)
    md = ["## Complementary & traditional options", "",
          "**Evidence-based options** (proven or limited evidence, no safety signal):", ""]
    if evidence:
        md += [f"- {r['item']} — {r['verdict']}" + (f" ({r['sources']})" if r["sources"] else "")
               for r in evidence]
    else:
        md.append("- (none has good evidence for this topic yet)")
    md += ["", "**Popular but not proven** (shown for honesty, never as a recommendation):", ""]
    if popular:
        for r in popular:
            flag = "⛔ " if r["verdict"] == "Risky / harmful" else ""
            md.append(f"- {flag}{r['item']} — {r['verdict']}")
    else:
        md.append("- (none)")
    return md + [""]


_CITE_RUN = re.compile(r"\[S\d+\](?:[ \t]*\[S\d+\])*")


def _collapse_cites(text: str) -> str:
    """One source once per adjacent citation run: [S2][S2][S2] → [S2]. Only runs of
    citations are touched, so text elsewhere (and distinct runs) is unchanged."""
    def dedupe(run):
        seen, out = set(), []
        for ws, cite in re.findall(r"([ \t]*)(\[S\d+\])", run.group(0)):
            if cite not in seen:               # keep its first place, drop repeats
                seen.add(cite)
                out.append(ws + cite)
        return "".join(out)
    return _CITE_RUN.sub(dedupe, text)


def _link_refs(text: str, facts: dict) -> str:
    """[F12] in an editor's overview → that fact's source citations (no repeats)."""
    linked = re.sub(r"\[?(F\d+)\]?", lambda m: _cites(facts[m.group(1)]) if m.group(1) in facts else "", text or "")
    return _collapse_cites(linked)


def verified_facts(state: dict) -> list:
    return [f for f in state.get("facts", []) if f.get("status") == "verified"]


def used_sources(state: dict) -> list:
    ids = []
    for f in verified_facts(state):
        fids = list(f.get("source_ids", []))
        for m in f.get("also_supported_by", []) or []:
            fids += list(m.get("source_ids", []))
        for i in fids:
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
    header = f"# 🔬 RESEARCH DOSSIER — {state.get('topic', '')}"
    if state.get("topic_type"):
        header += f" · {state['topic_type']}"
    md = [header, ""]
    scores = state.get("coverage_scores") or {}
    md.append(f"Review round {state.get('review_round', 0)} · {len(facts)} verified facts from "
              f"{len(used_sources(state))} sources · coverage {scores.get('coverage', '?')}/10, "
              f"accuracy {scores.get('accuracy', '?')}/10, value {scores.get('value', '?')}/10"
              + ("" if state.get("coverage_pass") else " · ⚠️ coverage check not passed (see notes)"))
    md += ["", "> Every fact below was extracted from the cited source's own text, matched word-for-word "
           "to a quote, and checked by a separate verifier model. Edit **review.md** to ask for changes.", ""]
    if state.get("angle"):
        md += [f"**Angle:** {state['angle']}", ""]
    classic = state.get("classic_checks") or []
    special = state.get("special_groups") or []
    if classic or special:
        md += ["## Plan", ""]
        if classic:
            md += ["**Classic checks:** " + " · ".join(
                f"{c['item']} ({c['section']})" if c.get("section") else str(c["item"])
                for c in classic if isinstance(c, dict) and c.get("item")), ""]
        if special:
            md += ["**Special groups:** " + " · ".join(str(g) for g in special), ""]
    rows = classic_check_table(state)
    if rows:
        md += _classic_table_markdown(rows)
    comp = _complementary(state)
    if comp.get("applies"):
        md += _complementary_markdown(rows)
    elif "complementary" in state:              # the planner made an explicit decision
        md += ["Complementary medicine: not applicable" + (f" — {comp['why']}" if comp.get("why") else ""),
               ""]
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
                strength = f.get("strength", "n/a")
                label = STRENGTH_LABELS.get(strength) if strength and strength != "n/a" else ""
                extra = f" · {label}" if label else ""
                if label and f.get("strength_reason"):
                    extra += f" ({f['strength_reason']})"
                also = f.get("also_supported_by") or []
                if also:
                    names = _source_names(state, [i for m in also for i in m.get("source_ids", [])])
                    if names:
                        extra += f" (also: {names})"
                md.append(f"- **{f['id']}** {f['claim']} {_cites(f)} · "
                          f"{LEVEL_BADGE.get(f.get('level', 6), '')}{extra}")
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
    rows = classic_check_table(state)
    if rows:
        out.append("CLASSIC CHECKS (item → evidence verdict → fact ids):")
        out += [f"- {r['item']} → {r['verdict']} → {', '.join(r['fact_ids']) or '—'}" for r in rows]
        out.append("")
    comp = _complementary(state)
    if comp.get("applies"):
        evidence, popular = complementary_lists(rows)
        out.append("COMPLEMENTARY OPTIONS — evidence-based (safe to present as options):")
        out += [f"- {r['item']} — {r['verdict']}" for r in evidence] or \
               ["- (none has good evidence for this topic yet)"]
        out.append("")
        out.append("COMPLEMENTARY — popular but NOT proven (never present as an option):")
        if popular:
            out += [f"- {'RISKY — ' if r['verdict'] == 'Risky / harmful' else ''}{r['item']} — {r['verdict']}"
                    for r in popular]
        else:
            out.append("- (none)")
        out.append("")
    for sec in state.get("outline", []):
        sec_facts = [f for f in facts if f["section"] == sec["id"]]
        if not sec_facts:
            continue
        out.append(f"SECTION {sec['id']}: {sec['title']}")
        for kind in ("fact", "advice", "red_flag", "myth", "debated"):
            for f in (x for x in sec_facts if x["kind"] == kind):
                label = {"myth": "MYTH vs EVIDENCE", "red_flag": "RED FLAG", "advice": "PRACTICAL",
                         "debated": "DEBATED"}.get(kind, "FACT")
                strength = f.get("strength", "n/a")
                tag = f" [evidence: {strength}]" if strength and strength != "n/a" else ""
                out.append(f"- {label}: {f['claim']} {_cites(f)}{tag}")
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

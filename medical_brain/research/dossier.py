"""The research dossier: the verified facts, organised by the outline, every one with
its sources — written by code from the verified facts, so nothing unverified can slip
in. Also the review file the creator edits, and the source text the video workflow
is built from."""
import hashlib
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

# The patient guide's action card: emergency first so it can't be missed.
GUIDE_ACTION_LABELS = (("emergency_if", "🚨 Go to the ER if"),
                       ("do_now", "Do this now"),
                       ("daily", "Tonight / before exercise"),
                       ("works", "What works (with evidence)"),
                       ("doesnt_work_or_risky", "Doesn't work or is risky"),
                       ("see_doctor_if", "See a doctor if"))

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


def expert_checklist_rows(state: dict) -> list:
    """One row per expert-brief point: the point, its confidence and the coverage verdict.

    ``status`` is ``covered`` / ``contradicted`` / ``missing`` (unknown → missing); ``facts``
    are the verified fact ids the critic tied to it (always verified)."""
    brief = state.get("expert_brief") or {}
    status = state.get("expert_status") or {}
    rows = []
    for point in brief.get("points") or []:
        if not isinstance(point, dict):
            continue
        pid = str(point.get("id", "") or "").strip()
        entry = status.get(pid) or {}
        rows.append({"id": pid, "point": str(point.get("point", "") or "").strip(),
                     "confidence": str(point.get("confidence", "") or "").strip(),
                     "status": str(entry.get("status", "") or "missing"),
                     "facts": [str(f) for f in (entry.get("facts") or []) if f]})
    return rows


def _expert_status_text(row: dict) -> str:
    facts = [f for f in (row.get("facts") or []) if f]
    cite = ", ".join(facts)
    if row.get("status") == "covered":
        return f"✅ confirmed by {cite}" if cite else "✅ confirmed by the sources"
    if row.get("status") == "contradicted":
        return f"⚠️ contradicted by {cite}" if cite else "⚠️ contradicted by the sources"
    return "❓ not confirmed by the sources"


def _expert_markdown(state: dict) -> list:
    rows = expert_checklist_rows(state)
    if not rows:
        return []
    md = ["## Expert checklist — what a specialist expects vs what the sources confirmed", "",
          "| Point | What a specialist expects | Confidence | Status |", "|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['id']} | {r['point'].replace('|', '/')} | {r['confidence']} | "
                  f"{_expert_status_text(r)} |")
    md += ["", "Points marked ❓ or ⚠️ are NOT in the verified facts or the patient guide — "
           "the doctor decides whether they matter.", ""]
    return md


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


def _fact_source_ids(f: dict) -> list:
    """Every source a fact cites, including the ones merged into it (also_supported_by)."""
    ids = list(f.get("source_ids", []))
    for m in f.get("also_supported_by", []) or []:
        ids += list(m.get("source_ids", []))
    return ids


def _guide_cites(state: dict, fact_ids) -> str:
    """The [S#] citations of a guide item, via the facts it cites."""
    facts = {f["id"]: f for f in verified_facts(state)}
    out = []
    for fid in fact_ids or []:
        f = facts.get(str(fid).strip().strip("[]"))
        if not f:
            continue
        for sid in _fact_source_ids(f):
            if sid not in out:
                out.append(sid)
    return "".join(f"[{i}]" for i in out)


def _guide_md_label(item: dict) -> str:
    label = STRENGTH_LABELS.get(str(item.get("strength", "") or ""))
    return f" · {label}" if label else ""


def _guide_src_label(item: dict) -> str:
    strength = str(item.get("strength", "") or "")
    return f" [evidence: {strength}]" if strength else ""


def _guide_cited_fact_ids(guide: dict) -> list:
    ids = []

    def add(items):
        for it in items or []:
            if not isinstance(it, dict):
                continue
            for fid in it.get("facts", []) or []:
                fid = str(fid).strip().strip("[]")
                if fid and fid not in ids:
                    ids.append(fid)

    ac = guide.get("action_card") or {}
    for key, _ in GUIDE_ACTION_LABELS:
        add(ac.get(key))
    for sec in guide.get("sections", []) or []:
        add((sec or {}).get("points") if isinstance(sec, dict) else None)
    add(guide.get("special_groups"))
    add(guide.get("myths"))
    add(guide.get("uncertain"))
    add(guide.get("key_numbers"))
    comp = guide.get("complementary") or {}
    add(comp.get("options"))
    add(comp.get("not_proven"))
    return ids


def _guide_source_ids(state: dict, guide: dict) -> list:
    facts = {f["id"]: f for f in verified_facts(state)}
    out = []
    for fid in _guide_cited_fact_ids(guide):
        f = facts.get(fid)
        if not f:
            continue
        for sid in _fact_source_ids(f):
            if sid in state.get("sources", {}) and sid not in out:
                out.append(sid)
    return sorted(out, key=lambda i: int(re.sub(r"\D", "", i) or 0))


def _guide_item_line(state: dict, item: dict, text: str, prefix: str = "- ") -> str:
    return f"{prefix}{text.strip()} {_guide_cites(state, item.get('facts'))}{_guide_md_label(item)}".rstrip()


def render_guide_markdown(state: dict) -> str:
    """patient_guide.md — the organised, patient-valued version, every item citing
    the verified facts it came from and their evidence label. Built only from code,
    so nothing unverified can slip in."""
    guide = state.get("patient_guide") or {}
    report = state.get("guide_report") or {}
    n_facts = len(verified_facts(state))
    md = [f"# 🩺 PATIENT GUIDE — {state.get('topic', '')}", ""]
    if guide.get("promise"):
        md += [f"> **The promise:** {guide['promise']}", ""]
    md += ["## 🚨 Action card", ""]
    ac = guide.get("action_card") or {}
    for key, label in GUIDE_ACTION_LABELS:
        items = ac.get(key) or []
        md += [f"### {label}", ""]
        if items:
            md += [_guide_item_line(state, it, it.get("text", "")) for it in items]
        else:
            md.append("- (none)")
        md.append("")
    if guide.get("sections"):
        md += ["## What you need to know", ""]
        for sec in guide["sections"]:
            md += [f"### {sec.get('title', '')}", ""]
            if sec.get("question"):
                md += [f"*{sec['question']}*", ""]
            md += [_guide_item_line(state, p, p.get("text", "")) for p in sec.get("points", [])]
            md.append("")
    if guide.get("special_groups"):
        md += ["## Special groups", ""]
        for g in guide["special_groups"]:
            text = f"**{g.get('group', '')}:** {g.get('advice', '')}"
            md.append(_guide_item_line(state, g, text))
        md.append("")
    if guide.get("myths"):
        md += ["## Myths vs evidence", ""]
        for m in guide["myths"]:
            md.append(f"- **Myth:** {m.get('myth', '').strip()}")
            md.append(_guide_item_line(state, m, f"**Truth:** {m.get('truth', '')}", prefix="  "))
        md.append("")
    comp = guide.get("complementary") or {}
    options = comp.get("options") or []
    not_proven = comp.get("not_proven") or []
    if options or not_proven:
        md += ["## Complementary options — what the evidence says", "",
               "**Evidence-based options** (proven or limited evidence, no safety signal):", ""]
        if options:
            md += [_guide_item_line(state, it, f"{it.get('item', '')} — {it.get('text', '')}") for it in options]
        else:
            md.append("- (none has good evidence for this topic yet)")
        md += ["", "**Popular but not proven** (shown for honesty, never as a recommendation):", ""]
        if not_proven:
            md += [_guide_item_line(state, it, f"{it.get('item', '')} — {it.get('text', '')}")
                   for it in not_proven]
        else:
            md.append("- (none)")
        md.append("")
    if guide.get("key_numbers"):
        md += ["## Key numbers", ""]
        md += [_guide_item_line(state, it, it.get("text", "")) for it in guide["key_numbers"]]
        md.append("")
    if guide.get("uncertain"):
        md += ["## What the evidence doesn't settle", ""]
        md += [_guide_item_line(state, it, it.get("text", "")) for it in guide["uncertain"]]
        md.append("")
    dropped = report.get("dropped", len(report.get("dropped_items", []) or []))
    md += ["---", "",
           f"Built only from {n_facts} verified facts in the Evidence Library (dossier.md); "
           f"{dropped} items were removed by automatic checks.", ""]
    ids = _guide_source_ids(state, guide)
    if ids:
        md += ["## References", ""] + [f"- {line}" for line in reference_lines(state, ids)] + [""]
    return "\n".join(md)


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
    md += _expert_markdown(state)
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

{guide_line}Read dossier.md (the verified Evidence Library), then fill in this file and run:
    python3 main.py --continue {session}

{check_block}Set APPROVED to yes when the research is complete and correct (nothing else needed).
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


def _load_state_beside(path: str) -> dict:
    """The research_state.json saved next to a review file, or {} (old sessions, tests)."""
    state_path = os.path.join(os.path.dirname(path), "research_state.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def points_to_check(state: dict) -> list:
    """The ❓ / ⚠️ expert points the creator should verify (never covered ones)."""
    out = []
    for row in expert_checklist_rows(state):
        if row["status"] == "covered":
            continue
        out.append(f"- {_expert_status_text(row)} — **{row['id']}** ({row['confidence']}): {row['point']}")
    return out


def write_review_file(path: str, topic: str, session: str, has_guide: bool = False,
                      state: dict = None) -> None:
    if state is None:
        state = _load_state_beside(path)
    checks = points_to_check(state)
    check_block = ""
    if checks:
        check_block = ("Points to check yourself (a specialist would expect these; the sources did "
                       "not confirm them — they are NOT in the facts or the patient guide, so check "
                       "them yourself):\n" + "\n".join(checks) + "\n\n")
    guide_line = ("The **patient guide** (patient_guide.md) is what the video will be built from — "
                  "review it first.\n\n" if has_guide else "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(REVIEW_TEMPLATE.format(topic=topic, session=session, guide_line=guide_line,
                                       check_block=check_block))


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
    """Writes dossier.md, dossier.json and research_state.json (plus patient_guide.md
    and patient_guide.json when a guide exists). Returns their paths."""
    os.makedirs(folder, exist_ok=True)
    paths = {"md": os.path.join(folder, "dossier.md"), "json": os.path.join(folder, "dossier.json"),
             "state": os.path.join(folder, "research_state.json"),
             "guide_md": os.path.join(folder, "patient_guide.md"),
             "guide_json": os.path.join(folder, "patient_guide.json")}
    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write(render_markdown(state))
    dossier = {"topic": state.get("topic"), "outline": state.get("outline"), "facts": verified_facts(state),
               "sources": {i: state["sources"][i] for i in used_sources(state)},
               "rejected": state.get("rejected", []), "history": state.get("history", [])}
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)
    guide = state.get("patient_guide") or {}
    if guide:
        with open(paths["guide_md"], "w", encoding="utf-8") as f:
            f.write(render_guide_markdown(state))
        with open(paths["guide_json"], "w", encoding="utf-8") as f:
            json.dump({"patient_guide": guide, "report": state.get("guide_report", {})},
                      f, ensure_ascii=False, indent=2)
    with open(paths["state"], "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return paths


def _guide_video_source(state: dict, guide: dict) -> str:
    """The Patient Guide (Layer 2) as the source text of the video workflow."""
    out = [f"PATIENT GUIDE (Organised, patient-valued, verified) — TOPIC: {state.get('topic', '')}", ""]
    if state.get("angle"):
        out += [f"ANGLE: {state['angle']}", ""]
    if guide.get("promise"):
        out += [f"PROMISE: {guide['promise']}", ""]
    ac = guide.get("action_card") or {}
    out.append("ACTION CARD:")
    for key, label in GUIDE_ACTION_LABELS:
        items = ac.get(key) or []
        if not items:
            continue
        out.append(f"  {label.upper()}:")
        out += [f"  - {it.get('text', '').strip()} {_guide_cites(state, it.get('facts'))}"
                f"{_guide_src_label(it)}".rstrip() for it in items]
    out.append("")
    out.append("SECTIONS:")
    for sec in guide.get("sections", []) or []:
        out.append(f"SECTION: {sec.get('title', '')}")
        if sec.get("question"):
            out.append(f"  VIEWER QUESTION: {sec['question']}")
        out += [f"  - {p.get('text', '').strip()} {_guide_cites(state, p.get('facts'))}"
                f"{_guide_src_label(p)}".rstrip() for p in sec.get("points", []) or []]
        out.append("")
    if guide.get("special_groups"):
        out.append("SPECIAL GROUPS:")
        out += [f"  - {g.get('group', '')}: {g.get('advice', '').strip()} "
                f"{_guide_cites(state, g.get('facts'))}{_guide_src_label(g)}".rstrip()
                for g in guide["special_groups"]]
        out.append("")
    if guide.get("myths"):
        out.append("MYTHS:")
        for m in guide["myths"]:
            out.append(f"  - MYTH: {m.get('myth', '').strip()}")
            out.append(f"    TRUTH: {m.get('truth', '').strip()} {_guide_cites(state, m.get('facts'))}"
                       f"{_guide_src_label(m)}".rstrip())
        out.append("")
    comp = guide.get("complementary") or {}
    options, not_proven = comp.get("options") or [], comp.get("not_proven") or []
    verdicts = {str(r.get("item", "")).strip().lower(): r.get("verdict", "")
                for r in classic_check_table(state)}
    if options or not_proven:
        out.append("COMPLEMENTARY — evidence-based options (the only ones safe to present as options):")
        if options:
            out += [f"  - {it.get('item', '')} — {it.get('text', '').strip()} "
                    f"{_guide_cites(state, it.get('facts'))}{_guide_src_label(it)} "
                    f"(verdict: {verdicts.get(str(it.get('item', '')).strip().lower(), '')})".rstrip()
                    for it in options]
        else:
            out.append("  - (none has good evidence for this topic yet)")
        out.append("")
        out.append("COMPLEMENTARY — popular but NOT proven (never present as an option):")
        if not_proven:
            for it in not_proven:
                verdict = verdicts.get(str(it.get("item", "")).strip().lower(), "")
                flag = "RISKY — " if verdict == "Risky / harmful" else ""
                out.append(f"  - {flag}{it.get('item', '')} — {it.get('text', '').strip()} "
                           f"{_guide_cites(state, it.get('facts'))}{_guide_src_label(it)} "
                           f"(verdict: {verdict})".rstrip())
        else:
            out.append("  - (none)")
        out.append("")
    if guide.get("key_numbers"):
        out.append("KEY NUMBERS:")
        out += [f"  - {it.get('text', '').strip()} {_guide_cites(state, it.get('facts'))}"
                f"{_guide_src_label(it)}".rstrip() for it in guide["key_numbers"]]
        out.append("")
    if guide.get("uncertain"):
        out.append("UNCERTAIN (what the evidence does not settle):")
        out += [f"  - {it.get('text', '').strip()} {_guide_cites(state, it.get('facts'))}"
                f"{_guide_src_label(it)}".rstrip() for it in guide["uncertain"]]
        out.append("")
    out += ["SOURCES:"] + reference_lines(state, _guide_source_ids(state, guide))
    return "\n".join(out)


def video_source(state: dict) -> str:
    """The Patient Guide as the source text of the video workflow when it exists;
    otherwise the full dossier (old sessions). Both are English, every statement
    with its source ids — but here every claim is verified and cited."""
    guide = state.get("patient_guide") or {}
    if guide:
        return _guide_video_source(state, guide)
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


VIDEO_SOURCE_FILE = "video_source.md"


def write_video_source(state: dict, folder: str) -> str:
    """Write rd.video_source(state) to folder/video_source.md so the creator can edit
    the text the video script is built from. Stores the sha256 of the exact written
    text in state["video_source_sha256"] and returns the path."""
    text = video_source(state)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, VIDEO_SOURCE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    state["video_source_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return path


def read_video_source(state: dict, folder: str) -> tuple:
    """The source text the video is built from: (text, edited).

    Returns the creator's edited video_source.md (and True) when it exists and differs
    from the copy this code wrote (state["video_source_sha256"]); otherwise the text
    generated from the research state (and False). An empty/whitespace-only edit falls
    back to the generated text."""
    path = os.path.join(folder, VIDEO_SOURCE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            file_text = f.read()
    except OSError:
        return video_source(state), False
    if not file_text.strip():
        return video_source(state), False
    digest = hashlib.sha256(file_text.encode("utf-8")).hexdigest()
    if digest != state.get("video_source_sha256"):
        return file_text, True
    return video_source(state), False


def uncited_lines(text: str) -> list:
    """The bullet lines of a video source that have no [S#] citation, so the truth
    check can warn about them. The trailing SOURCES: block and placeholder lines
    such as "- (none)" are ignored, and so are generated "- MYTH: ..." lines (their
    citation sits on the TRUTH line that follows)."""
    lines = (text or "").splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == "SOURCES:":
            cut = i
            break
    out = []
    for line in lines[:cut]:
        stripped = line.strip()
        if not stripped.startswith("- ") or stripped.startswith("- ("):
            continue
        if stripped.startswith("- MYTH:"):
            continue
        if not re.search(r"\[S\d+\]", stripped):
            out.append(stripped)
    return out


def key_sources(state: dict, n: int = 6) -> list:
    """The strongest sources (best evidence, most used) for a video description."""
    use = {}
    for f in verified_facts(state):
        for i in f.get("source_ids", []):
            use[i] = use.get(i, 0) + 1
    ranked = sorted(use, key=lambda i: (state["sources"][i].get("level", 6), -use[i]))
    return reference_lines(state, [i for i in ranked if i in state.get("sources", {})][:n])

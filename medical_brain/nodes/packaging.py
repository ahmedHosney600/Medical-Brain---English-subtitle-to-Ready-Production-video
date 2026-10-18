"""Packaging: titles and thumbnails (brainstorm → finalists → honesty/CTR audit).

System prompts live in prompts/<step>.md; the user prompts below insert the video's data."""

import re

from ..utils.overclaims import overclaim_in
from ..llm import call_llm, call_llm_json
from ..prompts import load_prompt
from ..state import PipelineState
from ..utils.llm_json import UnreadableAnswer, pick



# Rules shared by the three packaging prompts (prompts/_shared/*.md).
EGYPTIAN_CTR_RULES = load_prompt("_shared/egyptian_ctr_rules")
DEFAULT_THUMBNAIL_FACE_RULES = load_prompt("_shared/thumbnail_face_rules")

# Words that mean the thumbnail asks for an extreme face (checked in code).
EXTREME_FACE_WORDS = [
    "shocked", "shock face", "gasp", "jaw drop", "jaw-drop", "mouth open", "open mouth", "open-mouthed",
    "wide-eyed", "wide eyes", "eyes wide", "bulging", "screaming", "scream", "horrified", "terrified",
    "panicked", "hands on cheeks", "hands on face", "exaggerated expression", "extreme expression",
    "mrbeast", "صدمة على وشه", "مخضوض", "مفزوع",
]


TITLE_MAX_CHARS = 60
# Absolute claims a title may make only when the script says exactly that.
_ABSOLUTE_RE = re.compile(r"(هو|هي)\s+السبب|بيشفي|يشفي|هيشفي|نهائي[اًا]?|للأبد|مضمون|100\s*%|١٠٠\s*٪|أكيد\s+(?:هـ|ه)")
# Words in a search phrase that do not name the condition.
_NOT_TOPIC = {"علاج", "ازاي", "اعرف", "اثناء", "أثناء", "خلال", "ليه", "امتي", "إمتى", "اسباب", "أسباب", "طريقه",
              "طريقة", "كيف", "في", "من", "علي", "على", "عن", "مع", "او", "أو", "و", "وعلاجه", "وعلاجها", "اعراض",
              "أعراض", "الحل", "حل", "سبب", "يعني", "ايه", "إيه", "النوم", "بالليل", "الطبيعي", "البيت"}


def _bare_word(word: str) -> str:
    from ..exports.workbook import normalize_ar
    w = normalize_ar(word)
    for prefix in ("وال", "بال", "ال", "و", "ب"):
        if w.startswith(prefix) and len(w) - len(prefix) >= 3:
            return w[len(prefix):]
    return w


def topic_words(keyword: str) -> set:
    """The words of the SEO phrase that name the condition («الصداع النصفي» in «ازاي اعرف
    الصداع النصفي وعلاجه»), without "ال"/"و" prefixes."""
    from ..exports.workbook import normalize_ar
    words = {_bare_word(w) for w in (keyword or "").split()
             if len(w) >= 3 and normalize_ar(w) not in {normalize_ar(x) for x in _NOT_TOPIC}}
    return {w for w in words if len(w) >= 3}


def names_topic(title: str, keyword: str) -> bool:
    wanted = topic_words(keyword)
    return not wanted or bool(wanted & {_bare_word(w) for w in (title or "").split()})


def thumbnail_texts(thumbnail_text: str) -> list:
    """The text overlays of the thumbnail concepts ("Bold Arabic Text Overlay ...: `"…"`")."""
    out = []
    for line in (thumbnail_text or "").splitlines():
        if "text overlay" in line.lower():
            m = re.search(r"[`\"«“]{1,2}([^`\"»”]{2,80})[`\"»”]{1,2}", line.split(":", 1)[-1])
            if m:
                out.append(m.group(1).strip())
    return out


def thumbnail_face_rules(state) -> str:
    return (state.get("thumbnail_face_rules") or "").strip() or DEFAULT_THUMBNAIL_FACE_RULES


def check_thumbnail_faces(thumbnail_text: str) -> list:
    """Code check: extreme-expression wording in the thumbnail concepts, outside
    the negative prompts (where those words are supposed to appear)."""
    problems = []
    for line in (thumbnail_text or "").splitlines():
        low = line.lower()
        positive = re.split(r"negative prompt|negatives?:|avoid:|no exaggerat|not allowed", low)[0]
        hits = [w for w in EXTREME_FACE_WORDS if w in positive and not re.search(r"\b(no|not|avoid|without)\b[^.]{0,40}" + re.escape(w), positive)]
        if hits:
            problems.append(f"extreme expression requested ({', '.join(hits)}): \"{line.strip()[:140]}\"")
    return problems


def packaging_creative_director(state: PipelineState) -> dict:
    system_prompt = load_prompt("packaging_creative_director", EGYPTIAN_CTR_RULES=EGYPTIAN_CTR_RULES, FACE_RULES=thumbnail_face_rules(state))

    user_prompt = f"""Brainstorm packaging concepts for this finished video.

FINAL LOCKED SCRIPT:
{state.get("refined_script", "")}

MEDICAL TOPIC: {state.get("medical_topic", "")}
SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
SEO SECONDARY KEYWORDS: {state.get("seo_secondary_keywords", "")}
SEO SEARCH INTENT: {state.get("seo_search_intent", "")}

REAL SEARCH DATA (YouTube autocomplete + keyword research):
{state.get("seo_research_output", "")}

RESTRUCTURE STRATEGY PLAN (for tone/audience context):
{state.get("strategy_plan", "")}

PRESENTER: {state.get("presenter_profile", "")}

AVOID LIST:
{state.get("avoid_list", "")}

Brainstorm 12-15 concepts across at least 8 mechanisms, each anchored to something real in the script above. Recommend the strongest 8-10."""

    response = call_llm(system_prompt, user_prompt, temperature=0.9, max_tokens=7000)
    return {"packaging_brainstorm_output": response}


def packaging_generator(state: PipelineState) -> dict:
    system_prompt = load_prompt("packaging_generator", EGYPTIAN_CTR_RULES=EGYPTIAN_CTR_RULES, FACE_RULES=thumbnail_face_rules(state))

    correction_note = state.get("packaging_critique_output", "") if state.get("packaging_revision_count", 0) > 0 else ""

    user_prompt = f"""Turn this brainstorm into finalists.

PACKAGING BRAINSTORM:
{state.get("packaging_brainstorm_output", "")}

FINAL LOCKED SCRIPT (for fact-checking anchors):
{state.get("refined_script", "")}

SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
SEO SECONDARY KEYWORDS: {state.get("seo_secondary_keywords", "")}

REVISION COUNT: {state.get("packaging_revision_count", 0)}
{"PREVIOUS CRITIQUE — YOU MUST RESOLVE THESE SPECIFIC ISSUES:" + chr(10) + correction_note if correction_note else ""}
{("YOUR PREVIOUS FINALISTS (revise these: keep every title and thumbnail the critique didn't flag, word for word; fix or replace the flagged ones):" + chr(10) + state.get("title_options", "") + chr(10) + chr(10) + state.get("thumbnail_concepts", "")) if correction_note else ""}

Produce 8-10 finalist titles and exactly 5 fully-specified thumbnail concepts."""

    response = call_llm(system_prompt, user_prompt, temperature=0.7, max_tokens=10000)
    # Split the two sections out for downstream nodes that only need one or the other.
    title_section, _, thumb_section = response.partition("### Thumbnail Concepts")
    return {
        "title_options": title_section.strip(),
        "thumbnail_concepts": ("### Thumbnail Concepts" + thumb_section).strip() if thumb_section else response,
    }


def packaging_honesty_ctr_auditor(state: PipelineState) -> dict:
    system_prompt = load_prompt("packaging_honesty_ctr_auditor", EGYPTIAN_CTR_RULES=EGYPTIAN_CTR_RULES, FACE_RULES=thumbnail_face_rules(state))

    correction_note = f"\n\nREVISION COUNT: {state.get('packaging_revision_count', 0)} — verify prior issues were actually fixed." if state.get("packaging_revision_count", 0) > 0 else ""

    user_prompt = f"""Audit these packaging finalists against the actual finished script.

TITLE OPTIONS:
{state.get("title_options", "")}

THUMBNAIL CONCEPTS:
{state.get("thumbnail_concepts", "")}

FINAL LOCKED SCRIPT (the ground truth for Promise-Delivery Match):
{state.get("refined_script", "")}

SEO PRIMARY KEYWORD: {state.get("seo_primary_keyword", "")}
{correction_note}

Score every title and thumbnail. Apply the hard gates. Pick and rank the A/B test set. Output ONLY the JSON object."""

    try:
        data, response = call_llm_json(system_prompt, user_prompt, temperature=0.3, max_tokens=6000,
                                       required=[("packaging_grade", "grade"),
                                                 ("lowest_ab_promise_delivery_score", "top_promise_delivery_score")])
    except UnreadableAnswer as e:
        data, response = None, e.raw
    revision = state.get("packaging_revision_count", 0) + 1

    # Code checks that can't be talked past: enough candidates, natural faces.
    code_issues = []
    n_titles = len([l for l in state.get("title_options", "").splitlines()
                    if re.match(r"^\|\s*\d+\s*\|", l.strip())])
    if n_titles < 8:
        code_issues.append(f"Only {n_titles} finalist titles in the table; produce 8-10 from different concepts.")
    n_thumbs = len(re.findall(r"AI Image Generation Prompt", state.get("thumbnail_concepts", "")))
    if n_thumbs < 5:
        code_issues.append(f"Only {n_thumbs} fully specified thumbnails; produce exactly 5.")
    for problem in check_thumbnail_faces(state.get("thumbnail_concepts", "")):
        code_issues.append("PRESENTER FACE: " + problem + " — keep a natural, subtle, human expression.")
    if n_thumbs and "reference photo" not in state.get("thumbnail_concepts", "").lower():
        code_issues.append("The AI Image Generation Prompts must tell the image tool to use the presenter's reference photo and keep his natural expression.")

    title_rows = [l for l in state.get("title_options", "").splitlines() if re.match(r"^\|\s*\d+\s*\|", l.strip())]
    for row in title_rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        title = cells[1] if len(cells) > 1 else row
        phrase = overclaim_in(title)
        if phrase:
            code_issues.append(f"TITLE OVERCLAIM: «{title}» promises «{phrase}» — titles must not promise a speed "
                               "or certainty of effect; rewrite it to what the script delivers.")

    script = state.get("refined_script", "") or ""
    keyword = state.get("seo_primary_keyword", "") or ""
    for row in title_rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        title = cells[1] if len(cells) > 1 else row
        if len(title) > TITLE_MAX_CHARS:
            code_issues.append(f"TITLE LENGTH: «{title}» is {len(title)} characters; keep titles ≤ {TITLE_MAX_CHARS} "
                               "so the hook isn't cut off on phones.")
        absolute = _ABSOLUTE_RE.search(title)
        if absolute and absolute.group(0) not in script:
            code_issues.append(f"TITLE CERTAINTY: «{title}» says «{absolute.group(0)}», which the script never says "
                               "with that certainty — keep the script's strength (a question, «ممكن», «أحياناً»).")
    for overlay in thumbnail_texts(state.get("thumbnail_concepts", "")):
        if len(overlay.split()) > 4:
            code_issues.append(f"THUMBNAIL TEXT: «{overlay}» is {len(overlay.split())} words; use 2–4 words in the "
                               "viewer's own language.")

    text = response.strip()
    try:
        if data is None:
            raise ValueError("unreadable JSON answer")
        grade = str(pick(data, "packaging_grade", "grade", default="")).strip().upper()
        top_pd_score = pick(data, "lowest_ab_promise_delivery_score", "top_promise_delivery_score",
                            default=0, kind=int)
        if top_pd_score < 9 or grade != "PASS" or code_issues:
            grade = "NEEDS_REVISION"

        report = pick(data, "packaging_critique_report", "critique_report", "report", default="", kind=str) or text
        if code_issues:
            report += "\n\n### Code checks (must fix)\n" + "\n".join(f"- {i}" for i in code_issues)

        ab_titles = [str(t) for t in (pick(data, "ab_test_titles", default=[]) or []) if str(t).strip()]
        ab_thumbs = [str(t) for t in (pick(data, "ab_test_thumbnails", default=[]) or []) if str(t).strip()]
        ab_set = ""
        if ab_titles or ab_thumbs:
            ab_set = "**Titles to A/B test (ranked):**\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(ab_titles, 1))
            ab_set += "\n\n**Thumbnails to A/B test (ranked):**\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(ab_thumbs, 1))

        # Never recommend a title with time-to-effect or certainty wording ("تفكها فوراً").
        recommended = str(pick(data, "recommended_title", default="")).strip()
        if recommended and overclaim_in(recommended):
            honest = [t for t in ab_titles if not overclaim_in(t)]
            if honest:
                print(f"  ⚠️ recommended title overclaims; using «{honest[0]}» instead")
                recommended = honest[0]
        # The viewer must see which condition the video is about.
        if recommended and not names_topic(recommended, keyword):
            named = [t for t in ab_titles if names_topic(t, keyword) and not overclaim_in(t)]
            if named:
                print(f"  ⚠️ recommended title doesn't name the condition; using «{named[0]}» instead")
                recommended = named[0]
            else:
                code_issues.append(f"TOPIC: no A/B title names the condition ({' '.join(sorted(topic_words(keyword)))}); "
                                   "the recommended title must say what the video is about, naturally.")
                grade = "NEEDS_REVISION"
                report += "\n- " + code_issues[-1]

        return {
            "packaging_grade": grade,
            "packaging_critique_output": report,
            "recommended_title": recommended,
            "packaging_ab_test_set": ab_set,
            "packaging_revision_count": revision,
        }
    except Exception:
        report = response + ("\n\n### Code checks (must fix)\n" + "\n".join(f"- {i}" for i in code_issues) if code_issues else "")
        return {
            "packaging_grade": "NEEDS_REVISION",
            "packaging_critique_output": report,
            # No verdict this round: don't leave an older round's picks next to it.
            "recommended_title": "",
            "packaging_ab_test_set": "",
            "packaging_revision_count": revision,
        }

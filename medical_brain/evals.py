"""Phase 0 research eval harness: run a research session for a topic from the
frozen topic bank and append one comparable scorecard row.

  python3 -m medical_brain.evals run <slug> [--label L]
  python3 -m medical_brain.evals score <session_dir|id> --slug <slug> [--label L]
  python3 -m medical_brain.evals table

`run` does what `main.py --topic … --workflow research --provider main` does, then
scores the session (like `score`). `score` works on an existing session folder/id.
Scoring calls the research judge once (prompts/evals/research_judge.md) and computes
the rest of the metrics from the session itself, then appends a line to
evals/results.jsonl and regenerates evals/scorecard.md.

The patient questions for a topic are generated once (prompts/evals/patient_questions.md)
and written back into evals/topics.json; later runs are graded against the same list.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse

from dotenv import load_dotenv

from .llm import call_llm_json, load_llm_config
from .llm.config import load_keys
from .paths import PROJECT_ROOT
from .prompts import load_prompt
from .research import dossier as rd
from .utils.llm_json import UnreadableAnswer

EVALS_DIR = os.path.join(PROJECT_ROOT, "evals")
TOPICS_PATH = os.path.join(EVALS_DIR, "topics.json")
RESULTS_PATH = os.path.join(EVALS_DIR, "results.jsonl")
SCORECARD_PATH = os.path.join(EVALS_DIR, "scorecard.md")

SCORE_KEYS = ("answers", "actionability", "safety", "focus", "evidence_honesty")
BALANCE_URL = "https://api.deepseek.com/user/balance"


# --------------------------------------------------------------------------
# Topic bank + frozen patient questions
# --------------------------------------------------------------------------
def load_topics(path: str = None) -> dict:
    with open(path or TOPICS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_topics(data: dict, path: str = None) -> None:
    with open(path or TOPICS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_topic(data: dict, slug: str) -> dict:
    for topic in data.get("topics", []):
        if topic.get("slug") == slug:
            return topic
    return {}


def generate_questions(topic: str, topic_type: str) -> list:
    """One LLM call, JSON only: the frozen list used to grade every later run."""
    user = f"TOPIC: {topic}\nTYPE: {topic_type}"
    data, _raw = call_llm_json(load_prompt("evals/patient_questions"), user,
                               temperature=0.2, max_tokens=2000, required=["questions"])
    out = []
    for i, q in enumerate(data.get("questions") or [], 1):
        if not isinstance(q, dict):
            continue
        out.append({"id": q.get("id") or f"Q{i}",
                    "q": q.get("q") or q.get("question") or "",
                    "importance": (q.get("importance") or "useful")})
    return out


def ensure_questions(slug: str, path: str = None) -> list:
    """The topic's frozen questions: return them, or generate once and write them back."""
    path = path or TOPICS_PATH
    data = load_topics(path)
    topic = find_topic(data, slug)
    if not topic:
        raise SystemExit(f"slug '{slug}' is not in {path}")
    if topic.get("questions"):
        return topic["questions"]
    questions = generate_questions(topic.get("topic", ""), topic.get("type", ""))
    topic["questions"] = questions
    save_topics(data, path)
    print(f"🧊 Froze {len(questions)} patient questions for '{slug}' in {os.path.relpath(path, PROJECT_ROOT)}")
    return questions


# --------------------------------------------------------------------------
# Session reading
# --------------------------------------------------------------------------
def session_paths(session_dir: str) -> tuple:
    """(research folder, checkpoint path). Accepts 'output/<id>', '<id>' or any path."""
    candidates = [session_dir]
    if session_dir:
        candidates += [os.path.join(PROJECT_ROOT, session_dir),
                       os.path.join(PROJECT_ROOT, "output", session_dir)]
    for cand in candidates:
        folder = os.path.join(cand, "research")
        for name in ("research_checkpoint.json", "research_state.json"):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return folder, path
    raise SystemExit(f"no research session found for '{session_dir}' (expected <dir>/research/research_checkpoint.json)")


def load_session_state(session_dir: str) -> dict:
    _folder, path = session_paths(session_dir)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _run_minutes(folder: str) -> float:
    stamps = []
    path = os.path.join(folder, "research_log.jsonl")
    if not os.path.exists(path):
        return 0.0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                stamps.append(datetime.datetime.fromisoformat(json.loads(line)["timestamp"]))
            except Exception:
                continue
    if len(stamps) < 2:
        return 0.0
    return round((max(stamps) - min(stamps)).total_seconds() / 60, 1)


def _site(source: dict) -> str:
    """Domain of the source's url, or its origin when there is no url."""
    url = (source or {}).get("url") or ""
    if url:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    return (source or {}).get("origin") or ""


def fact_site(fact: dict, sources: dict) -> str:
    """The site of a fact's first source (its url's domain, or its origin)."""
    for sid in fact.get("source_ids", []):
        if sid in sources:
            return _site(sources[sid])
    return ""


def _web_site(fact: dict, sources: dict) -> str:
    for sid in fact.get("source_ids", []):
        source = sources.get(sid)
        if source and source.get("origin") == "Web":
            return _site(source)
    return ""


# --------------------------------------------------------------------------
# Judge
# --------------------------------------------------------------------------
def build_judge_user(topic: str, questions: list, facts: list, sources: dict) -> str:
    lines = [f"TOPIC: {topic}", "", "PATIENT QUESTIONS:"]
    for q in questions or []:
        lines.append(f"{q.get('id', '')} ({q.get('importance', '')}) {q.get('q', '')}")
    lines += ["", "VERIFIED FACTS:"]
    for f in facts or []:
        site = fact_site(f, sources)
        lines.append(f"{f.get('id', '')} [section {f.get('section', '')} · {f.get('kind', '')}] "
                     f"{f.get('claim', '')} — source: {site} (level {f.get('level', 6)})")
    return "\n".join(lines)


def judge_session(topic: str, questions: list, facts: list, sources: dict):
    """One judge call; on an unreadable answer retry once, then return None."""
    system = load_prompt("evals/research_judge")
    user = build_judge_user(topic, questions, facts, sources)
    problem = None
    for _ in range(2):
        try:
            judge, _raw = call_llm_json(system, user, temperature=0, max_tokens=8000,
                                        required=["questions", "scores"])
            return judge
        except UnreadableAnswer as e:
            problem = e
    print(f"  ⚠️ Judge answer unreadable after retry ({problem}); recording judge=null.")
    return None


# --------------------------------------------------------------------------
# Metrics (computed by code, not the LLM)
# --------------------------------------------------------------------------
def compute_metrics(state: dict, questions: list, judge: dict) -> dict:
    facts = rd.verified_facts(state)
    sources = state.get("sources", {}) or {}
    n_facts = len(facts)

    used = {i for f in facts for i in f.get("source_ids", []) if i in sources}
    web_facts = [f for f in facts
                 if any(sources.get(i, {}).get("origin") == "Web" for i in f.get("source_ids", []))]

    by_site: dict = {}
    for f in web_facts:
        site = _web_site(f, sources) or fact_site(f, sources)
        by_site[site] = by_site.get(site, 0) + 1
    by_site = dict(sorted(by_site.items(), key=lambda kv: (-kv[1], kv[0])))

    judge = judge or {}
    answered = {q.get("id"): q.get("answered") for q in judge.get("questions", []) if isinstance(q, dict)}
    questions = questions or []
    core = [q for q in questions if q.get("importance") == "core"]
    useful = [q for q in questions if q.get("importance") == "useful"]
    core_yes = sum(1 for q in core if answered.get(q.get("id")) == "yes")
    core_partly = sum(1 for q in core if answered.get(q.get("id")) == "partly")
    useful_yes = sum(1 for q in useful if answered.get(q.get("id")) == "yes")

    def count(name):
        return len(judge.get(name, []) or [])

    def pct(n):
        return round(100.0 * n / n_facts, 1) if n_facts else 0.0

    duplicates = [g for g in (judge.get("duplicates") or []) if isinstance(g, (list, tuple))]
    duplicate_facts = len({i for g in duplicates for i in g})

    scores = judge.get("scores") if isinstance(judge.get("scores"), dict) else {}
    score_values = [scores.get(k) for k in SCORE_KEYS]
    numeric = [v for v in score_values if isinstance(v, (int, float))]
    mean = round(sum(numeric) / len(numeric), 2) if len(numeric) == len(SCORE_KEYS) else None

    google = any(entry and entry[0] == "Google" for entry in state.get("source_log", []) if isinstance(entry, (list, tuple)))

    return {
        "verified_facts": n_facts,
        "left_out": len(state.get("rejected", []) or []),
        "sources_used": len(used),
        "facts_citing_web": len(web_facts),
        "web_share": pct(len(web_facts)),
        "facts_by_site": by_site,
        "sections": len(state.get("outline", []) or []),
        "run_minutes": None,  # filled from the session log by score_session
        "coverage_scores": state.get("coverage_scores", {}) or {},
        "google_status": "attempted" if google else "none",
        "core_answered": core_yes,
        "core_total": len(core),
        "core_partly": core_partly,
        "core_answered_pct": round(100.0 * core_yes / len(core), 1) if core else 0.0,
        "useful_answered": useful_yes,
        "useful_total": len(useful),
        "off_topic": count("off_topic"),
        "off_topic_pct": pct(count("off_topic")),
        "low_value": count("low_value"),
        "low_value_pct": pct(count("low_value")),
        "duplicates": len(duplicates),
        "duplicate_facts": duplicate_facts,
        "safety_gaps": count("safety_gaps"),
        "overclaims": count("overclaims"),
        "answers": scores.get("answers"),
        "actionability": scores.get("actionability"),
        "safety": scores.get("safety"),
        "focus": scores.get("focus"),
        "evidence_honesty": scores.get("evidence_honesty"),
        "score_mean": mean,
    }


# --------------------------------------------------------------------------
# Git + cost
# --------------------------------------------------------------------------
def _git(*args) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def fetch_deepseek_balance():
    """DeepSeek account balance in USD, or None when it can't be read. The key is
    loaded into the environment the way the rest of the project does it."""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return None
    try:
        request = urllib.request.Request(BALANCE_URL, headers={
            "Authorization": f"Bearer {key}", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    infos = data.get("balance_infos") or []
    for info in infos:
        if str(info.get("currency", "")).upper() == "USD":
            return _as_float(info.get("total_balance"))
    return _as_float(infos[0].get("total_balance")) if infos else None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def balance_delta(before, after):
    """cost_usd = before - after; null when either reading is unavailable.
    Approximate: any other DeepSeek usage during the run also moves the balance."""
    if before is None or after is None:
        return None
    return round(before - after, 6)


# --------------------------------------------------------------------------
# Results + scorecard
# --------------------------------------------------------------------------
def read_results(path: str = None) -> list:
    path = path or RESULTS_PATH
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def append_result(result: dict, path: str = None) -> None:
    path = path or RESULTS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def _fmt_cost(cost) -> str:
    return "—" if cost is None else f"${cost:.4f}"


def _fmt_num(value, digits=1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_scorecard(results: list, topics: dict = None) -> str:
    header = ("| date | label | slug | commit | minutes | cost | facts | left out | web % | core answered | "
              "off-topic % | low-value % | safety gaps | answers | actionability | safety | focus | honesty | mean |")
    sep = "|" + "---|" * 19
    lines = ["# Research eval scorecard", "",
             "One row per scored session. Baseline is the `v1-baseline-research-web` tag; rows are comparable "
             "only when the topic bank stayed frozen (same `questions`).", "", header, sep]
    for r in results:
        metrics = r
        lines.append("| {date} | {label} | {slug} | {commit} | {minutes} | {cost} | {facts} | {left} | {web} | "
                     "{core} | {off} | {low} | {safety} | {answers} | {action} | {safety_score} | {focus} | "
                     "{honesty} | {mean} |".format(
                         date=r.get("date", ""),
                         label=r.get("label", "") or "—",
                         slug=r.get("slug", ""),
                         commit=r.get("git_commit", "") or r.get("git_tag", ""),
                         minutes=_fmt_num(r.get("run_minutes")),
                         cost=_fmt_cost(r.get("cost_usd")),
                         facts=r.get("verified_facts", 0),
                         left=r.get("left_out", 0),
                         web=_fmt_num(r.get("web_share")),
                         core=f"{r.get('core_answered', 0)}/{r.get('core_total', 0)}",
                         off=_fmt_num(r.get("off_topic_pct")),
                         low=_fmt_num(r.get("low_value_pct")),
                         safety=r.get("safety_gaps", 0),
                         answers=_fmt_num(r.get("answers")),
                         action=_fmt_num(r.get("actionability")),
                         safety_score=_fmt_num(r.get("safety")),
                         focus=_fmt_num(r.get("focus")),
                         honesty=_fmt_num(r.get("evidence_honesty")),
                         mean=_fmt_num(r.get("score_mean"), 2)))
    lines += ["", "_`cost` is approximate: DeepSeek balance delta (before − after). Concurrent DeepSeek usage "
              "would inflate it; `—` means the balance was unavailable._", ""]

    latest = {}
    for r in results:
        latest[r.get("slug")] = r
    if latest:
        topics = topics if topics is not None else _safe_topics()
        by_slug = {t.get("slug"): t for t in topics.get("topics", [])}
        lines += ["## Latest per topic", ""]
        for slug, r in latest.items():
            commit = r.get("git_commit", "") or r.get("git_tag", "")
            lines.append(f"### `{slug}` — {r.get('label', '') or 'unlabelled'} · {r.get('date', '')} "
                         f"(commit {commit})")
            lines.append("")
            judge = r.get("judge") or {}
            lines.append(judge.get("summary") or "_No judge summary (judge output was unreadable)._")
            lines.append("")
            open_core = _open_core_questions(by_slug.get(slug, {}), judge)
            if open_core:
                lines.append("**Core questions not fully answered:**")
                lines += [f"- {qid} ({status}): {text}" for qid, status, text in open_core]
            else:
                lines.append("**All core questions answered.**")
            lines += ["", ""]
    return "\n".join(lines).rstrip() + "\n"


def _open_core_questions(topic: dict, judge: dict) -> list:
    answered = {q.get("id"): q.get("answered") for q in (judge or {}).get("questions", []) if isinstance(q, dict)}
    out = []
    for q in topic.get("questions", []) or []:
        if q.get("importance") != "core":
            continue
        status = answered.get(q.get("id"), "no")
        if status != "yes":
            out.append((q.get("id", ""), status, q.get("q", "")))
    return out


def _safe_topics() -> dict:
    try:
        return load_topics()
    except Exception:
        return {}


def write_scorecard(results: list = None, path: str = None, topics: dict = None) -> str:
    path = path or SCORECARD_PATH
    text = render_scorecard(results if results is not None else read_results(), topics)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


# --------------------------------------------------------------------------
# Scoring / running
# --------------------------------------------------------------------------
def score_session(session_dir: str, slug: str, label: str = "", cost_usd=None) -> dict:
    topics = load_topics()
    topic = find_topic(topics, slug)
    if not topic:
        raise SystemExit(f"slug '{slug}' is not in {os.path.relpath(TOPICS_PATH, PROJECT_ROOT)} "
                         f"(known: {', '.join(t.get('slug', '') for t in topics.get('topics', []))})")
    questions = ensure_questions(slug)
    folder, _state_path = session_paths(session_dir)
    state = load_session_state(session_dir)
    facts = rd.verified_facts(state)

    print(f"\n⚖️  Scoring {slug} — {len(facts)} verified facts against {len(questions)} questions …")
    judge = judge_session(topic.get("topic", ""), questions, facts, state.get("sources", {}))
    metrics = compute_metrics(state, questions, judge)
    metrics["run_minutes"] = _run_minutes(folder)

    result = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "label": label,
        "slug": slug,
        "type": topic.get("type", ""),
        "session": os.path.basename(os.path.normpath(session_dir)),
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_tag": _git("describe", "--tags", "--always"),
        "cost_usd": cost_usd,
        **metrics,
        "judge": judge,
    }

    with open(os.path.join(folder, "eval.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    append_result(result)
    write_scorecard()
    _print_summary(result)
    return result


def _print_summary(result: dict) -> None:
    judge = result.get("judge") or {}
    print(f"✅ Scored. core {result['core_answered']}/{result['core_total']} answered "
          f"(+{result['core_partly']} partly) · web facts {result['facts_citing_web']}/{result['verified_facts']} "
          f"({result['web_share']}%) · judge mean {result['score_mean']} · cost {_fmt_cost(result['cost_usd'])}")
    if judge.get("summary"):
        print(f"   {judge['summary']}")
    print(f"   Wrote {os.path.relpath(RESULTS_PATH, PROJECT_ROOT)} and {os.path.relpath(SCORECARD_PATH, PROJECT_ROOT)}")


def _configure_env() -> None:
    """Load .env and llm_keys.env the same way main.py / load_llm_config do."""
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    load_keys()


def _run_research(topic: str) -> str:
    """A full research-only session on provider mode 'main'; returns its folder."""
    from .cli import _new_session_dir, _research_settings, run_research, start_router
    from .research.state import new_state

    _configure_env()
    router = start_router(load_llm_config(), "main")
    session_id, session_dir = _new_session_dir()
    state = new_state(topic, "", "", _research_settings())
    state["workflow"] = "research"
    state["session"] = session_id
    run_research(router, state, session_dir)
    return session_dir


def cmd_run(slug: str, label: str = "") -> dict:
    _configure_env()
    topics = load_topics()
    topic = find_topic(topics, slug)
    if not topic:
        raise SystemExit(f"slug '{slug}' is not in {os.path.relpath(TOPICS_PATH, PROJECT_ROOT)}")
    before = fetch_deepseek_balance()
    session_dir = _run_research(topic.get("topic", ""))
    after = fetch_deepseek_balance()
    cost = balance_delta(before, after)
    if cost is None:
        print("💰 DeepSeek balance unavailable before/after; recording cost=null (approximate).")
    else:
        print(f"💰 DeepSeek balance delta: ${cost:.4f} (approximate).")
    return score_session(session_dir, slug, label, cost_usd=cost)


def cmd_score(session_dir: str, slug: str, label: str = "") -> dict:
    _configure_env()
    return score_session(session_dir, slug, label, cost_usd=None)


def cmd_table() -> str:
    text = write_scorecard()
    print(text)
    return text


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m medical_brain.evals",
                                     description="Research eval harness (Phase 0).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a full research session for a topic, then score it")
    p_run.add_argument("slug")
    p_run.add_argument("--label", default="")

    p_score = sub.add_parser("score", help="score an existing research session")
    p_score.add_argument("session")
    p_score.add_argument("--slug", required=True)
    p_score.add_argument("--label", default="")

    sub.add_parser("table", help="write and print evals/scorecard.md from evals/results.jsonl")

    args = parser.parse_args(argv)
    if args.command == "run":
        cmd_run(args.slug, args.label)
    elif args.command == "score":
        cmd_score(args.session, args.slug, args.label)
    else:
        cmd_table()


if __name__ == "__main__":
    main()

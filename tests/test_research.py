"""The topic → research workflow, offline: fake sources and a scripted fake model."""
import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock

from medical_brain import llm
from medical_brain.llm.progress import _Progress, quiet
from medical_brain.prompts import load_prompt
from medical_brain.research import dossier as rd
from medical_brain.research import nodes
from medical_brain.research.graph import build_research_app
from medical_brain.research.sources import europepmc, evidence, medlineplus, pubmed, web
from medical_brain.research.state import new_state

PUBMED_XML = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>111</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2023</Year></PubDate></JournalIssue><Title>Heart Rhythm</Title></Journal>
<ArticleTitle>Smartwatch detection of atrial fibrillation: a systematic review and meta-analysis</ArticleTitle>
<Abstract><AbstractText Label="RESULTS">Pooled sensitivity was 94.2% and specificity 95.3% for detecting atrial fibrillation.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Meta-Analysis</PublicationType></PublicationTypeList></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/HR.2023</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

EPMC = {"resultList": {"result": [{"id": "222", "source": "MED", "pmid": "222", "doi": "", "pubYear": "2021",
                                   "title": "Atrial fibrillation guideline update", "abstractText": "<p>Atrial fibrillation increases the risk of stroke about fivefold.</p>",
                                   "pubTypeList": {"pubType": ["Practice Guideline"]}, "citedByCount": 300,
                                   "journalInfo": {"journal": {"title": "Eur Heart J"}}}]}}

MEDLINE = """<nlmSearchResult><list><document url="https://medlineplus.gov/atrialfibrillation.html">
<content name="title">Atrial Fibrillation</content>
<content name="FullSummary">&lt;p&gt;Atrial fibrillation (AFib) is the most common type of arrhythmia.&lt;/p&gt;</content>
</document></list></nlmSearchResult>"""

SOURCE_A = {"key": "doi:10.1/a", "origin": "PubMed", "title": "Smartwatch AF meta-analysis", "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
            "journal": "Heart Rhythm", "year": 2023, "pub_types": ["Meta-Analysis"], "level": 2,
            "text": "Pooled sensitivity was 94.2% and specificity 95.3% for detecting atrial fibrillation."}
SOURCE_B = {"key": "url:https://medlineplus.gov/af", "origin": "MedlinePlus", "title": "Atrial Fibrillation",
            "url": "https://medlineplus.gov/af", "journal": "MedlinePlus (NIH)", "year": 0, "pub_types": [], "level": 5,
            "text": "See a doctor right away if you have chest pain, fainting or sudden shortness of breath."}


class SourceParsersTest(unittest.TestCase):
    def test_pubmed(self):
        [s] = pubmed.parse_records(PUBMED_XML)
        self.assertEqual((s["level"], s["year"], s["key"]), (2, 2023, "doi:10.1/hr.2023"))
        self.assertIn("94.2%", s["text"])

    def test_europepmc_and_medlineplus(self):
        [s] = europepmc.parse_results(EPMC)
        self.assertEqual((s["level"], s["cited_by"], s["text"]), (1, 300, "Atrial fibrillation increases the risk of stroke about fivefold."))
        [m] = medlineplus.parse_results(MEDLINE)
        self.assertEqual(m["text"], "Atrial fibrillation (AFib) is the most common type of arrhythmia.")

    def test_web_only_trusted_sites(self):
        self.assertTrue(web.is_trusted("https://www.nhs.uk/conditions/atrial-fibrillation/"))
        self.assertFalse(web.is_trusted("https://random-health-blog.com/af"))
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False), \
                mock.patch.dict(os.environ, {"BRAVE_API_KEY": ""}, clear=False):
            self.assertEqual(web.search("af"), [])

    def test_evidence_ranking(self):
        self.assertGreater(evidence.score({"level": 1, "year": 2023, "text": "x"}),
                           evidence.score({"level": 4, "year": 2023, "text": "x"}))

    def test_network_failure_returns_nothing(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("blocked")), mock.patch("time.sleep"):
            self.assertEqual(pubmed.search("af"), [])


class Scripted:
    """Answers each research step with canned JSON, recording the prompts."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def call(self, system, user, temperature=None, max_tokens=None):
        for name, answer in self.answers.items():
            if system == load_prompt("research/" + name):
                self.calls.append((name, user))
                return answer(user) if callable(answer) else answer
        raise AssertionError("unexpected prompt")


def fake_sources(kind, query, per_source, years):
    return [dict(SOURCE_A)] if kind == "literature" else [dict(SOURCE_B)]


class ResearchGraphTest(unittest.TestCase):
    def setUp(self):
        extract_calls = []

        def extract(user):
            extract_calls.append(user)
            good = {"kind": "fact", "claim": "Smartwatches detect AF with 94.2% sensitivity.",
                    "quote": "Pooled sensitivity was 94.2%", "source_ids": ["S1"]}
            flag = {"kind": "red_flag", "claim": "Chest pain or fainting needs a doctor right away.",
                    "quote": "See a doctor right away if you have chest pain, fainting", "source_ids": ["S2"]}
            made_up = {"kind": "fact", "claim": "Smartwatches cut stroke deaths by 50%.",
                       "quote": "Smartwatches cut stroke deaths in half", "source_ids": ["S1"]}
            return json.dumps({"facts": [good, flag] + ([made_up] if len(extract_calls) == 1 else [])})

        def verify(user):
            ids = [line.split(" ")[0] for line in user.splitlines() if line.startswith("F")]
            return json.dumps({"verdicts": [{"id": i, "verdict": "verified", "reason": "ok"} for i in ids]})

        self.extract_calls = extract_calls
        self.router = Scripted({
            "topic_planner": json.dumps({"angle": "What your watch can and can't tell you", "must_cover": ["accuracy"],
                                         "sections": [{"title": "How accurate are watches?", "questions": ["How accurate?"],
                                                       "literature_queries": ["smartwatch AF accuracy"], "public_queries": ["AF symptoms"]}]}),
            "source_screener": json.dumps({"sections": [{"id": "A", "keep": ["S1", "S2"], "gaps": []}]}),
            "fact_extractor": extract,
            "fact_verifier": verify,
            "coverage_critic": json.dumps({"coverage_score": 9, "accuracy_score": 9, "value_score": 9, "pass": True,
                                           "missing": [], "report": "complete"}),
            "dossier_editor": json.dumps({"summary": "Watches are good at spotting AF [F1]. They also cure it.",
                                          "section_notes": {"A": "Accuracy is high [F1]."}}),
            "review_planner": json.dumps({"new_sections": [{"title": "When to see a doctor", "questions": ["When?"],
                                                            "literature_queries": [], "public_queries": ["AF warning signs"]}],
                                          "edit_sections": [], "remove_sections": [], "remove_facts": []}),
        })
        llm.set_router(self.router)
        self.addCleanup(llm.set_router, None)
        patcher = mock.patch("medical_brain.research.sources.run_query", side_effect=fake_sources)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_graph(self, state):
        out = dict(state)
        for step in build_research_app().stream(state):
            for _, update in step.items():
                out.update(update or {})
        return out

    def test_full_research_round(self):
        state = self.run_graph(new_state("Smartwatches and atrial fibrillation"))
        verified = rd.verified_facts(state)
        claims = [f["claim"] for f in verified]
        self.assertIn("Smartwatches detect AF with 94.2% sensitivity.", claims)
        self.assertNotIn("Smartwatches cut stroke deaths by 50%.", claims)          # quote not in source
        self.assertTrue(any("cut stroke deaths" in f["claim"] for f in state["rejected"]))
        self.assertEqual(len(self.extract_calls), 2)        # first pass + one fix round
        self.assertEqual(len(claims), len(set(claims)))     # no duplicates from the re-extraction
        self.assertNotIn("cure", state["summary"])          # uncited sentence dropped
        md = rd.render_markdown(state)
        self.assertIn("[S1]", md)
        self.assertIn("Pooled sensitivity was 94.2%", md)
        src = rd.video_source(state)
        self.assertIn("RED FLAG: Chest pain or fainting needs a doctor right away. [S2]", src)
        self.assertIn("SOURCES:", src)

        # Review round: the creator asks for a new section → only that section is researched.
        state["review"] = rd.parse_review("APPROVED: no\nADD:\nwhen to see a doctor\nCHANGE:\n(…)\nREMOVE:\nNOTES:\n")
        before = len(self.extract_calls)
        state2 = self.run_graph(state)
        self.assertEqual([s["title"] for s in state2["outline"]], ["How accurate are watches?", "When to see a doctor"])
        self.assertIn("SECTION B: When to see a doctor", self.extract_calls[before])
        self.assertEqual(state2["review_round"], 1)

    def test_save_and_review_file(self):
        state = self.run_graph(new_state("Smartwatches and atrial fibrillation"))
        folder = tempfile.mkdtemp()
        paths = rd.save(state, folder)
        rd.write_review_file(os.path.join(folder, "review.md"), state["topic"], "output/x")
        with open(os.path.join(folder, "review.md")) as f:
            review = rd.parse_review(f.read())
        self.assertFalse(review["approved"])
        self.assertFalse(rd.has_requests(review))            # template placeholders don't count
        with open(paths["state"]) as f:
            self.assertEqual(json.load(f)["topic"], "Smartwatches and atrial fibrillation")
        self.assertTrue(rd.parse_review("APPROVED: yes\n")["approved"])


class QuoteCheckTest(unittest.TestCase):
    def test_numbers_and_quotes(self):
        state = {"sources": {"S1": dict(SOURCE_A)}}
        ok = {"claim": "Sensitivity was 94.2%.", "quote": "Pooled sensitivity was 94.2% and specificity", "source_ids": ["S1"]}
        self.assertEqual(nodes.quote_problem(ok, state), "")
        self.assertIn("not in the quote", nodes.quote_problem(dict(ok, claim="Sensitivity was 99%."), state))
        self.assertIn("not found", nodes.quote_problem(dict(ok, quote="Sensitivity was perfect in every study"), state))
        self.assertIn("no valid source", nodes.quote_problem(dict(ok, source_ids=["S9"]), state))

    def test_numbers_ignore_name_digits(self):
        self.assertEqual(nodes._numbers("CoQ10"), set())
        self.assertEqual(nodes._numbers("B12"), set())
        self.assertEqual(nodes._numbers("HbA1c"), set())
        self.assertEqual(nodes._numbers("SGLT2"), set())

    def test_numbers_keep_statistics(self):
        self.assertEqual(nodes._numbers("575 patients"), {"575"})
        self.assertEqual(nodes._numbers("95% CI -2.31 to -1.24"), {"95", "2.31", "1.24"})
        self.assertEqual(nodes._numbers("1,200"), {"1200"})
        self.assertEqual(nodes._numbers("65mg"), {"65"})
        self.assertEqual(nodes._numbers("COVID-19"), {"19"})
        self.assertEqual(nodes._numbers("type 2 diabetes"), {"2"})

    def test_quote_problem_accepts_name_digits(self):
        text = "Coenzyme Q10 was given daily. Supplementation reduced statin-associated muscle cramps in patients."
        state = {"sources": {"S1": {"text": text}}}
        fact = {"claim": "CoQ10 supplementation reduced statin-associated muscle cramps.",
                "quote": "Supplementation reduced statin-associated muscle cramps in patients.",
                "source_ids": ["S1"]}
        self.assertEqual(nodes.quote_problem(fact, state), "")


class FakeLlmJson:
    """Stands in for nodes.call_llm_json: records prompts, returns canned JSON."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, system, user, temperature=None, max_tokens=None, required=()):
        self.prompts.append(user)
        data = self.reply(user, len(self.prompts)) if callable(self.reply) else self.reply
        return data, json.dumps(data)


SOURCE = {"title": "A source", "journal": "Heart Rhythm", "year": 2023, "level": 2,
          "text": "Pooled sensitivity was 94.2% and specificity 95.3% for detecting atrial fibrillation."}


def section(sid="A", **kw):
    sec = {"id": sid, "title": f"Section {sid}", "questions": ["q?"], "queries": {},
           "pending": {"literature": [], "public": []}, "candidates": [], "source_ids": [], "extracted": []}
    sec.update(kw)
    return sec


def state_of(outline, **kw):
    st = {"topic": "T", "settings": {}, "outline": outline, "facts": [], "sources": {}, "work": []}
    st.update(kw)
    return st


class CoverageCriticTest(unittest.TestCase):
    def critic(self, data, **state):
        fake = FakeLlmJson(data)
        with mock.patch.object(nodes, "call_llm_json", fake):
            return nodes.coverage_critic(state_of([section("A")], **state)), fake

    def test_dedupe_and_merge_same_section(self):
        missing = [
            {"section_id": "A", "questions": ["Q1"], "literature_queries": ["q1"], "public_queries": ["p1"]},
            {"section_id": "A", "questions": ["Q1", "Q2"], "literature_queries": ["q2", "q1"], "public_queries": ["p2"]},
            {"section_id": "A", "questions": ["Q3"], "literature_queries": ["q3"], "public_queries": []},
        ]
        out, _ = self.critic({"coverage_score": 5, "accuracy_score": 5, "value_score": 5, "pass": False,
                              "missing": missing})
        self.assertTrue(out["need_research"])
        self.assertEqual(out["focus"], ["A"])                       # once, not three times
        self.assertEqual(out["work"], ["A"])
        sec = out["outline"][0]
        self.assertEqual(sec["pending"]["literature"], ["q1", "q2", "q3"])   # merged, not overwritten
        self.assertEqual(sec["pending"]["public"], ["p1", "p2"])
        self.assertEqual(sec["questions"], ["q?", "Q1", "Q2", "Q3"])          # deduped

    def test_caps_sections_per_round(self):
        outline = [section(s) for s in ("A", "B", "C", "D")]
        missing = [{"section_id": s, "literature_queries": [f"q{s}"], "questions": []}
                   for s in ("A", "B", "C", "D")]
        fake = FakeLlmJson({"coverage_score": 4, "accuracy_score": 4, "value_score": 4, "pass": False,
                            "missing": missing})
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.coverage_critic(state_of(outline))
        self.assertEqual(out["focus"], ["A", "B", "C"])             # first ones the critic listed

    def test_separate_new_sections_stay_separate(self):
        missing = [{"title": "New one", "questions": ["n1"], "public_queries": ["p1"]},
                   {"title": "New two", "questions": ["n2"], "public_queries": ["p2"]}]
        fake = FakeLlmJson({"coverage_score": 4, "accuracy_score": 4, "value_score": 4, "pass": False,
                            "missing": missing})
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.coverage_critic(state_of([section("A")]))
        self.assertEqual(len(out["outline"]), 3)                    # A + two new, not collapsed
        self.assertEqual(out["work"], ["B", "C"])

    def test_pass_rule_or_scores(self):
        one = [{"section_id": "A", "literature_queries": ["q"], "questions": []}]
        out, _ = self.critic({"coverage_score": 7, "accuracy_score": 7, "value_score": 7, "pass": False,
                              "missing": one})
        self.assertTrue(out["coverage_pass"])                       # all >= pass score is enough
        self.assertFalse(out["need_research"])
        out, _ = self.critic({"coverage_score": 6, "accuracy_score": 9, "value_score": 9, "pass": False,
                              "missing": one})
        self.assertFalse(out["coverage_pass"])
        self.assertTrue(out["need_research"])
        out, _ = self.critic({"coverage_score": 1, "accuracy_score": 1, "value_score": 1, "pass": True,
                              "missing": one})
        self.assertTrue(out["coverage_pass"])                       # model pass is enough

    def test_plateau_stops_research(self):
        one = [{"section_id": "A", "literature_queries": ["q"], "questions": []}]
        prev = {"coverage": 6, "accuracy": 6, "value": 6}
        fake = FakeLlmJson({"coverage_score": 6, "accuracy_score": 6, "value_score": 6, "pass": False,
                            "missing": one})
        with mock.patch.object(nodes, "call_llm_json", fake), mock.patch("builtins.print") as printed:
            out = nodes.coverage_critic(state_of([section("A")], coverage_round=1, coverage_scores=prev))
        self.assertFalse(out["need_research"])
        self.assertIn("scores stopped improving", " ".join(str(c) for c in printed.call_args_list))

    def test_improvement_keeps_researching(self):
        one = [{"section_id": "A", "literature_queries": ["q"], "questions": []}]
        out, _ = self.critic({"coverage_score": 7, "accuracy_score": 6, "value_score": 6, "pass": False,
                              "missing": one}, coverage_round=1,
                             coverage_scores={"coverage": 6, "accuracy": 6, "value": 6})
        self.assertTrue(out["need_research"])


class SourceScreenerTest(unittest.TestCase):
    def screen(self, sec, data, **state):
        fake = FakeLlmJson(data)
        st = state_of([sec], sources={f"S{i}": dict(SOURCE) for i in range(1, 15)},
                      focus=["A"], screen_round=0, work=["A"], **state)
        with mock.patch.object(nodes, "call_llm_json", fake):
            return nodes.source_screener(st), fake

    def test_source_ids_are_capped(self):
        sec = section("A", candidates=["S1", "S2", "S3", "S9", "S10"], source_ids=["S9", "S10"])
        out, _ = self.screen(sec, {"sections": [{"id": "A", "keep": ["S1", "S2", "S3"], "gaps": []}]},
                             settings={"max_sources_per_section": 4, "sources_per_section": 8})
        self.assertEqual(out["outline"][0]["source_ids"], ["S1", "S2", "S3", "S9"])

    def test_no_research_when_section_already_stocked(self):
        sec = section("A", candidates=[f"S{i}" for i in range(1, 9)],
                      source_ids=[f"S{i}" for i in range(1, 9)])
        out, _ = self.screen(sec, {"sections": [{"id": "A", "keep": [f"S{i}" for i in range(1, 9)],
                                                 "gaps": ["unanswered"], "literature_queries": ["q"]}]},
                             settings={"sources_per_section": 8})
        self.assertFalse(out["need_sources"])

    def test_research_when_gap_and_thin(self):
        sec = section("A", candidates=["S1"], source_ids=["S1"])
        out, _ = self.screen(sec, {"sections": [{"id": "A", "keep": ["S1"], "gaps": ["missing"],
                                                 "literature_queries": ["q"]}]},
                             settings={"sources_per_section": 8})
        self.assertTrue(out["need_sources"])
        self.assertEqual(out["focus"], ["A"])


class ExtractorLoopTest(unittest.TestCase):
    def run_node(self, state, reply):
        fake = FakeLlmJson(reply)
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.fact_extractor(state)
        return out, fake

    def test_second_pass_sends_only_new_sources_then_skips(self):
        sec = section("A", source_ids=["S1", "S2"], extracted=["S1"])
        st = state_of([sec], sources={"S1": dict(SOURCE), "S2": dict(SOURCE)},
                      facts=[], next_fact=1, to_extract=["A"])
        out, fake = self.run_node(st, {"facts": [{"claim": "New claim", "quote": "Pooled sensitivity was 94.2%",
                                                  "source_ids": ["S2"]}]})
        self.assertEqual(len(fake.prompts), 1)
        self.assertIn("[S2]", fake.prompts[0])
        self.assertNotIn("[S1]", fake.prompts[0])                   # already extracted
        self.assertEqual(out["outline"][0]["extracted"], ["S1", "S2"])

        st2 = {**st, **out, "to_extract": ["A"]}
        second = FakeLlmJson({"facts": []})
        with mock.patch.object(nodes, "call_llm_json", second), mock.patch("builtins.print") as printed:
            nodes.fact_extractor(st2)
        self.assertEqual(second.prompts, [])                        # no new sources → no LLM call
        self.assertIn("nothing new", " ".join(str(c) for c in printed.call_args_list))

    def test_fix_mode_only_cites_flagged_sources_and_adds_no_extras(self):
        fix = {"id": "F1", "section": "A", "kind": "fact", "claim": "Old claim", "quote": "bad quote",
               "source_ids": ["S2"], "level": 2, "status": "fix", "note": "quote not found"}
        done = {"id": "F0", "section": "A", "kind": "fact", "claim": "Existing claim", "quote": "x",
                "source_ids": ["S1"], "level": 2, "status": "verified", "note": ""}
        sec = section("A", source_ids=["S1", "S2"], extracted=["S1", "S2"])
        st = state_of([sec], sources={"S1": dict(SOURCE), "S2": dict(SOURCE)},
                      facts=[done, fix], next_fact=2, to_extract=["A"])
        reply = {"facts": [{"claim": "Corrected claim", "quote": "Pooled sensitivity was 94.2%",
                            "source_ids": ["S2"]},
                           {"claim": "Sneaky extra fact", "quote": "Pooled sensitivity was 94.2%",
                            "source_ids": ["S1"]}]}
        out, fake = self.run_node(st, reply)
        prompt = fake.prompts[0]
        self.assertIn("FIX MODE", prompt)
        self.assertIn("FIX THESE FACTS", prompt)
        self.assertIn("[S2]", prompt)
        self.assertNotIn("[S1]", prompt)                            # only the cited source is sent
        claims = [f["claim"] for f in out["facts"] if f["section"] == "A"]
        self.assertIn("Corrected claim", claims)
        self.assertNotIn("Sneaky extra fact", claims)               # corrections only
        self.assertNotIn("Old claim", claims)
        self.assertEqual(out["outline"][0]["extracted"], ["S1", "S2"])

    def test_fix_mode_links_correction_by_id_and_clears_rejected(self):
        fix = {"id": "F1", "section": "A", "kind": "fact", "claim": "Old claim", "quote": "bad quote",
               "source_ids": ["S2"], "level": 2, "status": "fix", "note": "quote not found"}
        unfixed = {"id": "F2", "section": "A", "kind": "fact", "claim": "Other bad claim", "quote": "bad",
                   "source_ids": ["S2"], "level": 2, "status": "fix", "note": "quote not found"}
        sec = section("A", source_ids=["S2"], extracted=["S2"])
        st = state_of([sec], sources={"S2": dict(SOURCE)}, facts=[fix, unfixed], next_fact=3, to_extract=["A"])
        reply = {"facts": [{"claim": "Reworded corrected claim", "quote": "Pooled sensitivity was 94.2%",
                            "source_ids": ["S2"], "fixes": "F1"},
                           {"claim": "A later repeat for F1", "quote": "Pooled sensitivity was 94.2%",
                            "source_ids": ["S2"], "fixes": "F1"}]}
        out, _ = self.run_node(st, reply)
        new = [f for f in out["facts"] if f.get("fixes") == "F1"]
        self.assertEqual(len(new), 1)                       # at most one correction per original
        self.assertEqual(new[0]["claim"], "Reworded corrected claim")
        self.assertEqual([f["id"] for f in out["facts"]], ["F3"])   # F1/F2 removed, link recorded
        rejected_ids = {r["id"] for r in out["rejected"]}
        self.assertNotIn("F1", rejected_ids)                # corrected → no longer "left out"
        self.assertIn("F2", rejected_ids)                   # no correction → still left out
        self.assertTrue(any(r["id"] == "F2" and "sent back for correction" in r["note"]
                            for r in out["rejected"]))

    def test_fix_mode_prompt_lists_original_ids(self):
        fix = {"id": "F7", "section": "A", "kind": "fact", "claim": "Old claim", "quote": "bad",
               "source_ids": ["S2"], "level": 2, "status": "fix", "note": "quote not found"}
        sec = section("A", source_ids=["S2"], extracted=["S2"])
        st = state_of([sec], sources={"S2": dict(SOURCE)}, facts=[fix], next_fact=8, to_extract=["A"])
        _, fake = self.run_node(st, {"facts": []})
        self.assertIn("- F7: Old claim", fake.prompts[0])   # the id travels with the fact

    def test_fact_budget_limits_new_facts(self):
        done = [{"id": f"F{i}", "section": "A", "kind": "fact", "claim": f"Existing {i}", "quote": "x",
                 "source_ids": ["S1"], "level": 2, "status": "verified", "note": ""} for i in range(2)]
        sec = section("A", source_ids=["S1"], extracted=[])
        st = state_of([sec], sources={"S1": dict(SOURCE)}, facts=done, next_fact=3, to_extract=["A"],
                      settings={"max_facts_per_section": 3})
        reply = {"facts": [{"claim": f"Fresh {i}", "quote": "Pooled sensitivity was 94.2%", "source_ids": ["S1"]}
                           for i in range(3)]}
        out, fake = self.run_node(st, reply)
        self.assertIn("EXTRACT AT MOST 1 NEW FACTS", fake.prompts[0])
        self.assertEqual(len([f for f in out["facts"] if f["section"] == "A"]), 3)   # 2 + 1

    def test_fact_budget_reached_skips_llm(self):
        done = [{"id": f"F{i}", "section": "A", "kind": "fact", "claim": f"Existing {i}", "quote": "x",
                 "source_ids": ["S1"], "level": 2, "status": "verified", "note": ""} for i in range(3)]
        sec = section("A", source_ids=["S1"], extracted=[])
        st = state_of([sec], sources={"S1": dict(SOURCE)}, facts=done, next_fact=4, to_extract=["A"],
                      settings={"max_facts_per_section": 3})
        fake = FakeLlmJson({"facts": []})
        with mock.patch.object(nodes, "call_llm_json", fake), mock.patch("builtins.print") as printed:
            nodes.fact_extractor(st)
        self.assertEqual(fake.prompts, [])
        self.assertIn("fact budget reached", " ".join(str(c) for c in printed.call_args_list))


class DossierCiteTest(unittest.TestCase):
    def link(self, text, source_ids):
        facts = {f"F{i}": {"source_ids": ids} for i, ids in enumerate(source_ids, 1)}
        return rd._link_refs(text, facts)

    def test_collapses_repeated_citations_in_one_run(self):
        self.assertEqual(self.link("[F1]", [["S2", "S2", "S2"]]), "[S2]")
        self.assertEqual(self.link("[F1] [F2]", [["S2"], ["S2"]]), "[S2]")
        self.assertEqual(self.link("[F1]", [["S2", "S7", "S2"]]), "[S2][S7]")

    def test_leaves_text_without_duplicates_unchanged(self):
        self.assertEqual(self.link("[F1]", [["S2", "S7"]]), "[S2][S7]")
        self.assertEqual(self.link("[F1] [F2]", [["S2"], ["S7"]]), "[S2] [S7]")
        self.assertEqual(self.link("See [F1] and later [F2].", [["S2"], ["S7"]]),
                         "See [S2] and later [S7].")


class ParallelCallsTest(unittest.TestCase):
    """parallel_calls: independent section calls run at once but merge in a fixed order."""

    def extract(self, workers, delay):
        outline = [section(sid, source_ids=[f"S{i + 1}"]) for i, sid in enumerate("ABCD")]
        st = state_of(outline, sources={f"S{i}": dict(SOURCE) for i in range(1, 5)},
                      facts=[], next_fact=1, to_extract=["A", "B", "C", "D"],
                      settings={"parallel_calls": workers})
        seen = []

        def reply(system, user, temperature=None, max_tokens=None, required=()):
            seen.append(llm.current_node.get())
            time.sleep(delay)
            sid = re.search(r"SECTION (\w+):", user).group(1)
            return {"facts": [{"claim": f"Claim {sid}", "quote": "Pooled sensitivity was 94.2%",
                               "source_ids": [f"S{ord(sid) - 64}"]}]}, "{}"

        with mock.patch.object(nodes, "call_llm_json", reply):
            out = llm.track_node("fact_extractor", nodes.fact_extractor)(st)
        return out, seen

    def test_calls_run_concurrently_with_the_right_node(self):
        start = time.monotonic()
        par, seen = self.extract(4, delay=0.2)
        self.assertLess(time.monotonic() - start, 0.7)      # would be 4 × 0.2s if sequential
        self.assertEqual(len(seen), 4)
        self.assertTrue(all(name == "fact_extractor" for name in seen))
        self.assertEqual([f["id"] for f in par["facts"]], ["F1", "F2", "F3", "F4"])

        seq, _ = self.extract(1, delay=0.2)                 # ids/order must match the sequential run
        key = lambda o: [(f["id"], f["section"], f["claim"]) for f in o["facts"]]   # noqa: E731
        self.assertEqual(key(par), key(seq))

    def test_source_ids_are_deterministic_across_worker_counts(self):
        def collect(workers):
            outline = [section(sid, pending={"literature": [f"q{sid}"], "public": []}) for sid in "ABCD"]
            st = state_of(outline, sources={}, focus=["A", "B", "C", "D"], queries_done=[],
                          settings={"parallel_calls": workers})

            def fake(kind, query, per_source, years):
                time.sleep(0.2)
                return [dict(SOURCE_A, key=f"doi:{query}")]

            with mock.patch("medical_brain.research.sources.run_query", side_effect=fake):
                return nodes.source_collector(st)

        start = time.monotonic()
        par = collect(4)
        self.assertLess(time.monotonic() - start, 0.7)      # would be 4 × 0.2s if sequential
        seq = collect(1)
        self.assertEqual([s["candidates"] for s in par["outline"]],
                         [s["candidates"] for s in seq["outline"]])
        self.assertEqual(par["outline"][0]["candidates"], ["S1"])   # ids follow outline order


class ProgressQuietTest(unittest.TestCase):
    def test_quiet_silences_the_live_line(self):
        p = _Progress("step", None)
        p.last = time.monotonic() - 10                      # make the next tick due
        with mock.patch("sys.stdout.write") as write:
            p.tick(writing=5)
        self.assertTrue(write.called)
        q = _Progress("step", None)
        q.last = time.monotonic() - 10
        with mock.patch("sys.stdout.write") as write, quiet():
            q.tick(writing=5)
        self.assertEqual(write.call_args_list, [])


class CliTest(unittest.TestCase):
    def test_find_latest_session_and_continue_without_requests(self):
        from medical_brain import cli
        root = tempfile.mkdtemp()
        folder = os.path.join(root, "output", "20260101_000000", "research")
        os.makedirs(folder)
        with open(os.path.join(folder, "research_state.json"), "w") as f:
            json.dump(new_state("topic"), f)
        rd.write_review_file(os.path.join(folder, "review.md"), "topic", "output/20260101_000000")
        with mock.patch.object(cli, "PROJECT_ROOT", root):
            self.assertTrue(cli._find_session("").endswith("20260101_000000"))
            with mock.patch("builtins.print") as printed:
                cli.continue_research(lambda: self.fail("no model needed"), "")
            self.assertIn("no requests", " ".join(str(c) for c in printed.call_args_list))


if __name__ == "__main__":
    unittest.main()

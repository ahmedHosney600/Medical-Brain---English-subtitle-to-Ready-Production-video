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
from medical_brain.research.graph import build_research_app, route_after_verifier
from medical_brain.research.sources import europepmc, evidence, google, medlineplus, pubmed, web
from medical_brain.research.state import new_state
from medical_brain.utils.llm_json import UnreadableAnswer

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

    def test_enabled_lists_google_when_chrome_available(self):
        from medical_brain.research.sources import enabled as sources_enabled
        with mock.patch("medical_brain.research.sources.browser.available", lambda: True):
            self.assertTrue(any("(+ Google)" in n for n in sources_enabled()))
        with mock.patch("medical_brain.research.sources.browser.available", lambda: False):
            self.assertFalse(any("(+ Google)" in n for n in sources_enabled()))

    def test_enabled_lists_sites_banner(self):
        from medical_brain.research.sources import enabled as sources_enabled
        banner = "Sites: Mayo Clinic, Johns Hopkins, NHS, Cleveland Clinic, NCCIH"
        with mock.patch("medical_brain.research.sources.browser.available", lambda: True):
            names = sources_enabled()
            self.assertIn(banner + " (+ Google)", names)
        with mock.patch("medical_brain.research.sources.browser.available", lambda: False):
            self.assertIn(banner, sources_enabled())

    def test_nccih_pages_are_level_5(self):
        self.assertEqual(evidence.level_of([], "Cupping", "https://www.nccih.nih.gov/health/cupping"), 5)
        self.assertEqual(evidence.level_of([], "Turmeric", "https://www.nccih.nih.gov/health/turmeric"), 5)

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
            "fact_merger": json.dumps({"groups": []}),
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
        state = self.run_graph(new_state("Smartwatches and atrial fibrillation",
                                        settings={"web_search": "off"}))
        verified = rd.verified_facts(state)
        claims = [f["claim"] for f in verified]
        self.assertIn("Smartwatches detect AF with 94.2% sensitivity.", claims)
        self.assertNotIn("Smartwatches cut stroke deaths by 50%.", claims)          # quote not in source
        self.assertTrue(any("cut stroke deaths" in f["claim"] for f in state["rejected"]))
        self.assertEqual(len(self.extract_calls), 2)        # first pass + one fix round
        self.assertEqual(len(claims), len(set(claims)))     # no duplicates from the re-extraction
        self.assertNotIn("cure", state["summary"])          # uncited sentence dropped
        names = [n for n, _ in self.router.calls]
        self.assertIn("fact_merger", names)                 # verifier → merger → critic
        self.assertLess(names.index("fact_merger"), names.index("coverage_critic"))
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
        state = self.run_graph(new_state("Smartwatches and atrial fibrillation",
                                        settings={"web_search": "off"}))
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


class PlannerFieldsTest(unittest.TestCase):
    def test_planner_stores_type_checks_and_groups(self):
        reply = {"topic_type": "symptom", "angle": "x",
                 "classic_checks": [{"item": "magnesium", "section": "B"},
                                    {"item": "", "section": "C"},
                                    "bad", {"section": "D"}],
                 "special_groups": ["pregnancy", " ", "children"],
                 "sections": [{"title": "S", "questions": ["q"], "literature_queries": ["l"],
                               "public_queries": ["p"]}]}
        fake = FakeLlmJson(reply)
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.topic_planner(new_state("t"))
        self.assertEqual(out["topic_type"], "symptom")
        self.assertEqual(out["classic_checks"], [{"item": "magnesium", "section": "B"}])
        self.assertEqual(out["special_groups"], ["pregnancy", "children"])

    def test_planner_stores_complementary_and_adds_items_to_checks(self):
        reply = {"classic_checks": [{"item": "cupping", "section": "B"}],
                 "complementary": {"applies": True, "why": "people try herbs",
                                   "items": ["cupping", "turmeric", " ", 5]},
                 "sections": [{"title": "Why it happens", "questions": ["q"], "literature_queries": [], "public_queries": []},
                              {"title": "Complementary and traditional options", "questions": ["q"],
                               "literature_queries": ["l"], "public_queries": ["p"]}]}
        fake = FakeLlmJson(reply)
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.topic_planner(new_state("t"))
        self.assertEqual(out["complementary"], {"applies": True, "why": "people try herbs",
                                                "items": ["cupping", "turmeric", "5"]})
        # "cupping" was already a check; turmeric is added to the complementary section.
        self.assertIn({"item": "cupping", "section": "B"}, out["classic_checks"])
        self.assertIn({"item": "turmeric", "section": "Complementary and traditional options"},
                      out["classic_checks"])
        self.assertEqual(len([c for c in out["classic_checks"] if c["item"].lower() == "cupping"]), 1)

    def test_planner_falls_back_to_last_section_for_a_forgotten_item(self):
        reply = {"complementary": {"applies": True, "why": "y", "items": ["honey"]},
                 "sections": [{"title": "First", "questions": ["q"], "literature_queries": [], "public_queries": []},
                              {"title": "Last one", "questions": ["q"], "literature_queries": [], "public_queries": []}]}
        fake = FakeLlmJson(reply)
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.topic_planner(new_state("t"))
        self.assertEqual(out["classic_checks"], [{"item": "honey", "section": "Last one"}])

    def test_defaults_and_old_checkpoint_still_works(self):
        st = new_state("t")
        self.assertEqual(st["topic_type"], "")
        self.assertEqual(st["classic_checks"], [])
        self.assertEqual(st["complementary"], {"applies": False, "why": "", "items": []})
        self.assertEqual(st["special_groups"], [])
        self.assertEqual(st["merged"], [])
        self.assertEqual(st["settings"]["max_facts_per_section"], 12)
        old = {"topic": "old", "outline": [], "facts": [], "sources": {}, "rejected": []}
        self.assertIn("RESEARCH DOSSIER", rd.render_markdown(old))
        self.assertEqual(rd.used_sources(old), [])


class VerifierStrengthTest(unittest.TestCase):
    def verify(self, verdicts, facts):
        st = state_of([section("A")], facts=facts, sources={"S1": dict(SOURCE)}, verify_round=0)
        fake = FakeLlmJson({"verdicts": verdicts})
        with mock.patch.object(nodes, "call_llm_json", fake):
            return nodes.fact_verifier(st)

    def test_strength_normalised_and_stored(self):
        base = {"section": "A", "kind": "fact", "quote": "Pooled sensitivity was 94.2%",
                "source_ids": ["S1"], "level": 2, "status": "new", "note": ""}
        facts = [dict(base, id="F1", claim="Magnesium helps cramps."),
                 dict(base, id="F2", claim="A cramp is sudden.")]
        out = self.verify([{"id": "F1", "verdict": "verified", "strength": "Probably helps",
                            "strength_reason": "one small RCT"},
                           {"id": "F2", "verdict": "verified", "strength": "banana"}], facts)
        by = {f["id"]: f for f in out["facts"]}
        self.assertEqual(by["F1"]["strength"], "probably_helps")
        self.assertEqual(by["F1"]["strength_reason"], "one small RCT")
        self.assertEqual(by["F2"]["strength"], "n/a")            # unknown label

    def test_strength_stored_when_wording_corrected(self):
        fact = {"id": "F1", "section": "A", "kind": "advice", "claim": "Magnesium cures cramps",
                "quote": "Pooled sensitivity was 94.2%", "source_ids": ["S1"], "level": 2,
                "status": "new", "note": ""}
        out = self.verify([{"id": "F1", "verdict": "fix", "corrected_claim": "Magnesium may help",
                            "reason": "overstated", "strength": "weak / disputed",
                            "strength_reason": "low certainty"}], [fact])
        f = out["facts"][0]
        self.assertEqual(f["status"], "verified")
        self.assertEqual(f["strength"], "weak_or_disputed")
        self.assertEqual(f["strength_reason"], "low certainty")

    def test_old_facts_without_strength_are_na(self):
        old = {"topic": "t", "outline": [section("A")],
               "sources": {"S1": {"title": "x", "journal": "j", "year": 2020, "level": 5,
                                  "url": "u", "text": "x"}},
               "facts": [{"id": "F1", "section": "A", "kind": "fact", "claim": "Stretch the calf.",
                          "quote": "x", "source_ids": ["S1"], "status": "verified", "level": 5}]}
        md = rd.render_markdown(old)
        self.assertIn("Stretch the calf.", md)
        self.assertNotIn("Proven", md)

    def test_untested_strength_is_accepted(self):
        base = {"section": "A", "kind": "fact", "quote": "Pooled sensitivity was 94.2%",
                "source_ids": ["S1"], "level": 2, "status": "new", "note": ""}
        facts = [dict(base, id="F1", claim="Cupping is used for pain."),
                 dict(base, id="F2", claim="Turmeric is used for arthritis.")]
        out = self.verify([{"id": "F1", "verdict": "verified", "strength": "untested",
                            "strength_reason": "no good studies"},
                           {"id": "F2", "verdict": "verified", "strength": "not studied",
                            "strength_reason": "no studies"}], facts)
        by = {f["id"]: f for f in out["facts"]}
        self.assertEqual(by["F1"]["strength"], "untested")
        self.assertEqual(by["F2"]["strength"], "untested")      # alias normalised too


class FactMergerTest(unittest.TestCase):
    SOURCES = {f"S{i}": {"title": f"Source {i}", "journal": f"journal{i}.org", "year": 2024,
                         "level": 3, "url": f"u{i}"} for i in range(1, 6)}

    def facts(self):
        mk = lambda i, sid, strength: {"id": f"F{i}", "section": "A" if i <= 3 else "B",
                                       "kind": "fact", "claim": f"Claim {i}", "quote": f"Quote {i}.",
                                       "source_ids": [sid], "level": 3, "status": "verified",
                                       "note": "", "strength": strength}   # noqa: E731
        return [mk(1, "S1", "proven"), mk(2, "S2", "proven"), mk(3, "S3", "probably_helps"),
                mk(4, "S4", "proven"), mk(5, "S5", "proven")]

    def merge(self, groups, **state):
        outline = [section("A"), section("B")]
        st = state_of(outline, sources=dict(self.SOURCES), facts=self.facts(), work=[],
                      rejected=[], merged=[], **state)
        fake = FakeLlmJson({"groups": groups})
        with mock.patch.object(nodes, "call_llm_json", fake):
            return nodes.fact_merger(st), fake

    def test_validates_groups_and_unions_sources(self):
        out, fake = self.merge([
            {"keep": "F1", "merge": ["F2", "F4"]},     # cross-section F4 dropped
            {"keep": "F3", "merge": ["F3"]},           # keep in merge → skipped
            {"keep": "F2", "merge": ["F3"]},           # F2 already used → skipped
            {"keep": "F4", "merge": ["F5"]},
            {"keep": "F9", "merge": ["F1"]},           # unknown keep → skipped
        ])
        self.assertEqual(len(fake.prompts), 2)          # one call per eligible section
        self.assertEqual([f["id"] for f in out["facts"]], ["F1", "F3", "F4"])
        merged = {m["id"]: m for m in out["merged"]}
        self.assertEqual(merged["F2"]["merged_into"], "F1")
        self.assertEqual(merged["F5"]["merged_into"], "F4")
        self.assertNotIn("F2", {f["id"] for f in out["facts"]})
        self.assertEqual(out["facts"][0]["source_ids"], ["S1", "S2"])
        self.assertEqual(out["facts"][0]["also_supported_by"],
                         [{"id": "F2", "source_ids": ["S2"], "quote": "Quote 2."}])
        self.assertEqual(out["facts"][2]["source_ids"], ["S4", "S5"])
        self.assertEqual(out.get("rejected", []), [])   # merged facts are not rejected
        self.assertNotIn("F2", {f["id"] for f in out.get("rejected", [])})

    def test_strength_kept_only_when_the_group_agrees(self):
        out, _ = self.merge([{"keep": "F1", "merge": ["F3"]},    # proven vs probably_helps
                             {"keep": "F4", "merge": ["F5"]}])   # proven + proven
        by = {f["id"]: f for f in out["facts"]}
        self.assertEqual(by["F1"]["strength"], "proven")         # disagreement → kept fact's
        self.assertEqual(by["F4"]["strength"], "proven")         # agreement

    def test_error_in_a_section_never_fails_the_run(self):
        outline = [section("A"), section("B")]
        st = state_of(outline, sources=dict(self.SOURCES), facts=self.facts(), work=[],
                      rejected=[], merged=[])

        def boom(system, user, temperature=None, max_tokens=None, required=()):
            raise UnreadableAnswer("bad", "raw")

        with mock.patch.object(nodes, "call_llm_json", boom), mock.patch("builtins.print"):
            out = nodes.fact_merger(st)
        self.assertEqual(out["merged"], [])
        self.assertEqual(len(out["facts"]), 5)

    def test_merger_carries_the_classic_check(self):
        facts = self.facts()
        facts[0]["check"] = "quinine"       # kept fact's own check wins
        facts[1]["check"] = "magnesium"     # F2 merged into F1
        st = state_of([section("A"), section("B")], sources=dict(self.SOURCES), facts=facts,
                      work=[], rejected=[], merged=[])
        fake = FakeLlmJson({"groups": [{"keep": "F1", "merge": ["F2"]}]})
        with mock.patch.object(nodes, "call_llm_json", fake):
            out = nodes.fact_merger(st)
        by = {f["id"]: f for f in out["facts"]}
        self.assertEqual(by["F1"]["check"], "quinine")

        facts2 = self.facts()               # kept has none; take the merged one's
        facts2[1]["check"] = "magnesium"
        st2 = state_of([section("A"), section("B")], sources=dict(self.SOURCES), facts=facts2,
                       work=[], rejected=[], merged=[])
        with mock.patch.object(nodes, "call_llm_json",
                               FakeLlmJson({"groups": [{"keep": "F1", "merge": ["F2"]}]})):
            out2 = nodes.fact_merger(st2)
        by2 = {f["id"]: f for f in out2["facts"]}
        self.assertEqual(by2["F1"]["check"], "magnesium")

    def test_route_verifier_to_merger_or_extractor(self):
        self.assertEqual(route_after_verifier({"need_fix": True}), "fact_extractor")
        self.assertEqual(route_after_verifier({"need_fix": False}), "fact_merger")
        self.assertEqual(route_after_verifier({}), "fact_merger")


class CriticPromptTest(unittest.TestCase):
    def test_prompt_lists_plan_and_strength_tags(self):
        facts = [{"id": "F12", "section": "A", "kind": "advice", "claim": "Magnesium may help.",
                  "status": "verified", "strength": "proven"},
                 {"id": "F13", "section": "A", "kind": "fact", "claim": "A cramp is sudden.",
                  "status": "verified"}]
        fake = FakeLlmJson({"coverage_score": 8, "accuracy_score": 8, "value_score": 8,
                            "pass": True, "missing": []})
        st = state_of([section("A")], facts=facts, topic_type="symptom",
                      classic_checks=[{"item": "magnesium", "section": "B"},
                                      {"item": "quinine", "section": ""}],
                      special_groups=["pregnancy"])
        with mock.patch.object(nodes, "call_llm_json", fake):
            nodes.coverage_critic(st)
        user = fake.prompts[0]
        self.assertIn("TOPIC TYPE: symptom", user)
        self.assertIn("CLASSIC CHECKS:", user)
        self.assertIn("- magnesium (B)", user)
        self.assertIn("- quinine", user)
        self.assertIn("— No evidence found in the sources searched", user)   # unresolved shown
        self.assertIn("SPECIAL GROUPS: pregnancy", user)
        self.assertIn("- F12 [advice · proven] Magnesium may help.", user)
        self.assertIn("- F13 [fact] A cramp is sudden.", user)

    def test_prompt_shows_the_current_check_verdict(self):
        facts = [{"id": "F12", "section": "A", "kind": "advice", "claim": "Magnesium may help.",
                  "status": "verified", "strength": "proven", "check": "magnesium"}]
        fake = FakeLlmJson({"coverage_score": 8, "accuracy_score": 8, "value_score": 8,
                            "pass": True, "missing": []})
        st = state_of([section("A")], facts=facts,
                      classic_checks=[{"item": "magnesium", "section": "A"}])
        with mock.patch.object(nodes, "call_llm_json", fake):
            nodes.coverage_critic(st)
        self.assertIn("- magnesium (A) — Helps (proven)", fake.prompts[0])


class DossierStrengthTest(unittest.TestCase):
    def state(self):
        return {
            "topic": "Cramps", "topic_type": "symptom",
            "classic_checks": [{"item": "magnesium", "section": "A"}],
            "special_groups": ["pregnancy"],
            "outline": [section("A")],
            "sources": {"S1": {"title": "Mayo", "journal": "mayoclinic.org", "year": 2024,
                               "level": 5, "url": "u1"},
                        "S2": {"title": "NHS", "journal": "nhs.uk", "year": 2024,
                               "level": 5, "url": "u2"}},
            "facts": [{"id": "F1", "section": "A", "kind": "advice", "claim": "Stretch the calf.",
                       "quote": "Stretch the calf muscle.", "source_ids": ["S1"], "level": 5,
                       "strength": "proven", "strength_reason": "Cochrane review", "status": "verified",
                       "also_supported_by": [{"id": "F2", "source_ids": ["S2"],
                                              "quote": "Stretching helps."}]}],
            "rejected": [],
            "merged": [{"id": "F2", "section": "A", "claim": "Calf stretching helps.",
                        "source_ids": ["S2"], "status": "verified", "merged_into": "F1"}],
        }

    def test_render_shows_labels_plan_and_merged_sources(self):
        md = rd.render_markdown(self.state())
        self.assertIn("symptom", md.splitlines()[0])
        self.assertIn("**Classic checks:** magnesium (A)", md)
        self.assertIn("**Special groups:** pregnancy", md)
        self.assertIn("✅ Proven", md)
        self.assertIn("(Cochrane review)", md)
        self.assertIn("[S1][S2]", md)
        self.assertIn("(also: nhs.uk)", md)
        self.assertNotIn("Calf stretching helps.", md)      # merged fact not a separate entry

    def test_video_source_has_evidence_tags_and_merged_sources(self):
        src = rd.video_source(self.state())
        self.assertIn("Stretch the calf. [S1][S2] [evidence: proven]", src)
        self.assertIn("[S2] NHS", src)                      # reference includes merged source


def _mk_fact(fid, check, strength, sid="S1", section="A"):
    return {"id": fid, "section": section, "kind": "fact", "claim": f"{check} claim {fid}",
            "quote": "supporting quote", "source_ids": [sid], "level": 2,
            "status": "verified", "strength": strength, "check": check}


class ClassicCheckTableTest(unittest.TestCase):
    SOURCES = {"S1": {"title": "Cochrane review", "journal": "cochranelibrary.com", "year": 2024,
                      "level": 2, "url": "u1"}}
    CHECKS = [{"item": "magnesium", "section": "A"}, {"item": "quinine", "section": "A"},
              {"item": "cupping", "section": "C"}]

    def state(self, facts, **kw):
        st = {"topic": "Cramps", "outline": [section("A")], "sources": dict(self.SOURCES),
              "facts": facts, "rejected": [], "classic_checks": list(self.CHECKS),
              "complementary": {"applies": True, "why": "people use them", "items": ["cupping"]},
              "settings": {}, "work": []}
        st.update(kw)
        return st

    def rows(self, facts, **kw):
        return {r["item"]: r for r in rd.classic_check_table(self.state(facts, **kw))}

    def test_harmful_overrides_proven(self):
        rows = self.rows([_mk_fact("F1", "magnesium", "proven"),
                          _mk_fact("F2", "magnesium", "harmful"),
                          _mk_fact("F3", "quinine", "proven")])
        self.assertEqual(rows["magnesium"]["verdict"], "Risky / harmful")
        self.assertEqual(rows["quinine"]["verdict"], "Helps (proven)")

    def test_each_lower_verdict_branch(self):
        for strength, verdict in (("proven", "Helps (proven)"),
                                  ("probably_helps", "May help (limited evidence)"),
                                  ("no_benefit", "Doesn't help"),
                                  ("weak_or_disputed", "Unclear / disputed"),
                                  ("untested", "Not studied well")):
            rows = self.rows([_mk_fact("F1", "magnesium", strength)])
            self.assertEqual(rows["magnesium"]["verdict"], verdict, strength)

    def test_no_facts_and_all_na(self):
        rows = self.rows([])
        self.assertEqual(rows["magnesium"]["verdict"], "No evidence found in the sources searched")
        self.assertEqual(rows["magnesium"]["fact_ids"], [])
        rows = self.rows([_mk_fact("F1", "magnesium", "n/a")])
        self.assertEqual(rows["magnesium"]["verdict"], "See facts")
        self.assertEqual(rows["magnesium"]["fact_ids"], ["F1"])
        self.assertEqual(rows["magnesium"]["source_ids"], ["S1"])

    def test_old_fact_without_a_strength_counts_as_na(self):
        fact = _mk_fact("F1", "magnesium", "n/a")
        del fact["strength"]
        rows = self.rows([fact])
        self.assertEqual(rows["magnesium"]["verdict"], "See facts")

    def test_complementary_flag(self):
        rows = self.rows([])
        self.assertTrue(rows["cupping"]["complementary"])
        self.assertFalse(rows["magnesium"]["complementary"])


class DossierComplementaryTest(unittest.TestCase):
    SOURCES = {"S1": {"title": "Cochrane review", "journal": "cochranelibrary.com", "year": 2024,
                      "level": 2, "url": "u1"},
               "S2": {"title": "NCCIH", "journal": "nccih.nih.gov", "year": 2024, "level": 5, "url": "u2"}}

    CHECKS = [{"item": "magnesium", "section": "A"}, {"item": "acupuncture", "section": "C"},
              {"item": "cupping", "section": "C"}, {"item": "black seed", "section": "C"}]
    COMP = {"applies": True, "why": "people try them", "items": ["acupuncture", "cupping", "black seed"]}
    FACTS = [_mk_fact("F1", "magnesium", "proven"),
             _mk_fact("F2", "acupuncture", "proven"),
             _mk_fact("F3", "cupping", "untested", sid="S2"),
             _mk_fact("F4", "black seed", "harmful", sid="S2")]

    def state(self, **kw):
        st = {"topic": "Cramps", "outline": [section("A")], "sources": dict(self.SOURCES),
              "facts": [dict(f) for f in self.FACTS], "rejected": [], "classic_checks": list(self.CHECKS),
              "complementary": dict(self.COMP), "settings": {}, "work": []}
        st.update(kw)
        return st

    def test_render_applies_true_two_lists(self):
        md = rd.render_markdown(self.state())
        self.assertIn("## Classic checks — what the evidence says", md)
        self.assertIn("| magnesium | Helps (proven) | F1 [S1] |", md)
        evidence = md.split("**Evidence-based options**")[1].split("**Popular but not proven**")[0]
        self.assertIn("acupuncture — Helps (proven)", evidence)
        self.assertNotIn("cupping", evidence)                  # untested never an option
        self.assertNotIn("black seed", evidence)               # harmful never an option
        popular = md.split("**Popular but not proven**")[1]
        self.assertIn("cupping — Not studied well", popular)
        self.assertIn("⛔ black seed — Risky / harmful", popular)

    def test_render_applies_false_is_one_line(self):
        md = rd.render_markdown(self.state(complementary={"applies": False, "why": "no such remedies here",
                                                          "items": []}))
        self.assertIn("Complementary medicine: not applicable — no such remedies here", md)
        self.assertNotIn("## Complementary & traditional options", md)

    def test_render_applies_false_without_a_reason_has_no_dangling_dash(self):
        md = rd.render_markdown(self.state(complementary={"applies": False, "why": "", "items": []}))
        self.assertIn("Complementary medicine: not applicable\n", md)
        self.assertNotIn("not applicable —", md)

    def test_render_without_complementary_key_is_quiet(self):
        state = self.state()
        del state["complementary"]
        md = rd.render_markdown(state)
        self.assertNotIn("Complementary medicine: not applicable", md)
        self.assertIn("## Classic checks — what the evidence says", md)

    def test_video_source_lists_verdicts_and_only_proven_options(self):
        src = rd.video_source(self.state())
        self.assertIn("CLASSIC CHECKS", src)
        self.assertIn("magnesium → Helps (proven) → F1", src)
        self.assertIn("cupping → Not studied well → F3", src)
        self.assertIn("black seed → Risky / harmful → F4", src)
        options = src.split("COMPLEMENTARY OPTIONS")[1].split("COMPLEMENTARY — popular but NOT proven")[0]
        self.assertIn("acupuncture — Helps (proven)", options)
        self.assertNotIn("cupping", options)
        self.assertNotIn("black seed", options)
        popular = src.split("COMPLEMENTARY — popular but NOT proven")[1]
        self.assertIn("cupping — Not studied well", popular)
        self.assertIn("RISKY — black seed — Risky / harmful", popular)


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

    def test_check_matches_classic_item_case_insensitively_and_unknown_is_dropped(self):
        sec = section("A", source_ids=["S1"])
        st = state_of([sec], sources={"S1": dict(SOURCE)}, facts=[], next_fact=1, to_extract=["A"],
                      classic_checks=[{"item": "Magnesium", "section": "A"},
                                      {"item": "Quinine", "section": "B"}])
        reply = {"facts": [
            {"claim": "Magnesium may help cramps.", "quote": "Pooled sensitivity was 94.2%",
             "source_ids": ["S1"], "check": "magnesium"},
            {"claim": "Quinine is used for cramps.", "quote": "Pooled sensitivity was 94.2%",
             "source_ids": ["S1"], "check": "QUININE"},
            {"claim": "Pickle juice may help.", "quote": "Pooled sensitivity was 94.2%",
             "source_ids": ["S1"], "check": "pickle juice"}]}
        out, fake = self.run_node(st, reply)
        self.assertIn("CLASSIC CHECKS: Magnesium | Quinine", fake.prompts[0])   # section A first
        by = {f["claim"]: f for f in out["facts"]}
        self.assertEqual(by["Magnesium may help cramps."]["check"], "Magnesium")   # canonical text
        self.assertEqual(by["Quinine is used for cramps."]["check"], "Quinine")
        self.assertNotIn("check", by["Pickle juice may help."])                    # unknown dropped

    def test_fix_mode_keeps_the_original_check(self):
        fix = {"id": "F7", "section": "A", "kind": "fact", "claim": "Old claim", "quote": "bad",
               "source_ids": ["S2"], "level": 2, "status": "fix", "note": "quote not found",
               "check": "magnesium"}
        sec = section("A", source_ids=["S2"], extracted=["S2"])
        st = state_of([sec], sources={"S2": dict(SOURCE)}, facts=[fix], next_fact=8, to_extract=["A"],
                      classic_checks=[{"item": "magnesium", "section": "A"}])
        reply = {"facts": [{"claim": "Corrected claim", "quote": "Pooled sensitivity was 94.2%",
                            "source_ids": ["S2"], "fixes": "F7"}]}
        out, _ = self.run_node(st, reply)
        self.assertEqual([f["id"] for f in out["facts"]], ["F8"])
        self.assertEqual(out["facts"][0]["check"], "magnesium")      # preserved from the original

    def test_fix_mode_correction_can_supply_its_own_check(self):
        fix = {"id": "F7", "section": "A", "kind": "fact", "claim": "Old claim", "quote": "bad",
               "source_ids": ["S2"], "level": 2, "status": "fix", "note": "quote not found",
               "check": "magnesium"}
        sec = section("A", source_ids=["S2"], extracted=["S2"])
        st = state_of([sec], sources={"S2": dict(SOURCE)}, facts=[fix], next_fact=8, to_extract=["A"],
                      classic_checks=[{"item": "magnesium", "section": "A"},
                                      {"item": "quinine", "section": "A"}])
        reply = {"facts": [{"claim": "Corrected claim", "quote": "Pooled sensitivity was 94.2%",
                            "source_ids": ["S2"], "fixes": "F7", "check": "Quinine"}]}
        out, _ = self.run_node(st, reply)
        self.assertEqual(out["facts"][0]["check"], "quinine")


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


WEB_A = {"title": "Mayo leg cramps", "href": "https://www.google.com/goto?url=A",
         "site": "mayoclinic.org", "snippet": "causes and self-care", "rank": 1}
WEB_B = {"title": "NHS leg cramps", "href": "https://www.google.com/goto?url=B",
         "site": "nhs.uk", "snippet": "treatment", "rank": 2}
SITE_A = {"title": "Mayo site leg cramps", "href": "https://www.mayoclinic.org/symptoms/night-leg-cramps",
          "site": "mayoclinic.org", "snippet": "self-care at night", "rank": 1}


class WebLlm:
    """Routes call_llm_json by system prompt; records every system used."""

    def __init__(self, triage, picker):
        self.triage = triage
        self.picker = picker
        self.triage_calls = 0
        self.systems = []

    def __call__(self, system, user, temperature=None, max_tokens=None, required=()):
        self.systems.append(system)
        if system == load_prompt("research/web_triage"):
            self.triage_calls += 1
            data = self.triage(user, self.triage_calls)
        elif system == load_prompt("research/passage_picker"):
            data = self.picker(user)
        else:
            raise AssertionError("unexpected prompt: " + system)
        return data, json.dumps(data)


class WebResearchTest(unittest.TestCase):
    """Google → triage → open → passage pick: fake google + fake LLM, no browser/network."""

    def setUp(self):
        self.blocks = {
            "<html>A</html>": ["## Night leg cramps",
                               "Most night leg cramps are harmless and have no known cause.",
                               "Stretching your calves before bed may help prevent them.",
                               "See a doctor if the cramps are frequent or severe."],
            "<html>B</html>": ["## Leg cramps",
                               "A cramp is a sudden tight contraction of a muscle.",
                               "They often happen at night and are usually harmless.",
                               "Drink enough water during the day to help prevent them."],
        }
        self.pages = {
            WEB_A["href"]: {"url": "https://www.mayoclinic.org/leg-cramps",
                            "title": "Mayo leg cramps", "html": "<html>A</html>"},
            WEB_B["href"]: {"url": "https://www.nhs.uk/leg-cramps",
                            "title": "NHS leg cramps", "html": "<html>B</html>"},
        }
        self.searches, self.opens = [], []
        self.site_searches = []
        google.reset_run()
        for target, new in (("medical_brain.research.sources.run_query", mock.Mock(return_value=[])),
                            ("medical_brain.research.sources.browser.available", mock.Mock(return_value=True))):
            patcher = mock.patch(target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def default_triage(self, user, call_no):
        sids = re.findall(r"## SECTION (\w+):", user)
        return {"sections": [{"id": s, "open": [1, 2], "why": "ok", "better_queries": []} for s in sids]}

    def default_picker(self, user):
        return {"keep": [1, 2] if "Mayo" in user else [2, 3], "relevance": "high"}

    def use_google(self, hits, triage=None, picker=None):
        def search(query, sites=None, max_results=10):
            self.searches.append(query)
            return [dict(r) for r in hits.get(query, [])]

        def fetch_page(result_or_url):
            href = result_or_url.get("href") if isinstance(result_or_url, dict) else result_or_url
            self.opens.append(href)
            return dict(self.pages[href]) if href in self.pages else None

        def paragraphs(html):
            return list(self.blocks.get(html, []))

        for name, fn in (("search", search), ("fetch_page", fetch_page), ("paragraphs", paragraphs)):
            patcher = mock.patch("medical_brain.research.sources.google." + name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        llm = WebLlm(triage or self.default_triage, picker or self.default_picker)
        patcher = mock.patch.object(nodes, "call_llm_json", llm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_site_search(self, site_hits):
        def search(query):
            self.site_searches.append(query)
            return [dict(r) for r in site_hits.get(query, [])]
        return search

    def _fake_fetch(self):
        def fetch_page(result_or_url):
            href = result_or_url.get("href") if isinstance(result_or_url, dict) else result_or_url
            self.opens.append(href)
            return dict(self.pages[href]) if href in self.pages else None

        def paragraphs(html):
            return list(self.blocks.get(html, []))
        for name, fn in (("fetch_page", fetch_page), ("paragraphs", paragraphs)):
            patcher = mock.patch("medical_brain.research.sources.google." + name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def use_sites(self, hits, triage=None, picker=None):
        """Site-native search only; page fetching still goes through google.fetch_page."""
        patcher = mock.patch("medical_brain.research.sources.sitesearch.search",
                             self._fake_site_search(hits))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._fake_fetch()
        llm = WebLlm(triage or self.default_triage, picker or self.default_picker)
        patcher = mock.patch.object(nodes, "call_llm_json", llm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_both(self, site_hits, google_hits, triage=None, picker=None):
        def google_search(query, sites=None, max_results=10):
            self.searches.append(query)
            return [dict(r) for r in google_hits.get(query, [])]

        patcher = mock.patch("medical_brain.research.sources.sitesearch.search",
                             self._fake_site_search(site_hits))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("medical_brain.research.sources.google.search", google_search)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._fake_fetch()
        llm = WebLlm(triage or self.default_triage, picker or self.default_picker)
        patcher = mock.patch.object(nodes, "call_llm_json", llm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_triage_open_and_verbatim_source(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "google", "web_pages_per_section": 2})
        self.use_google({"leg cramps causes": [WEB_A, WEB_B]})
        out = nodes.source_collector(st)
        self.assertEqual(out["sources"]["S1"]["text"],
                         "## Night leg cramps\nMost night leg cramps are harmless and have no known cause.")
        self.assertEqual(out["sources"]["S1"]["origin"], "Web")
        self.assertEqual(out["sources"]["S2"]["text"],
                         "A cramp is a sudden tight contraction of a muscle.\n"
                         "They often happen at night and are usually harmless.")
        self.assertEqual(self.opens, [WEB_A["href"], WEB_B["href"]])
        self.assertIn("google:leg cramps causes", out["queries_done"])
        self.assertEqual(out["outline"][0]["candidates"], ["S1", "S2"])

    def test_unreadable_triage_falls_back_to_top_k(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "google", "web_pages_per_section": 1})

        def bad_triage(user, call_no):
            raise UnreadableAnswer("nope", "raw")

        self.use_google({"leg cramps causes": [WEB_A, WEB_B]}, triage=bad_triage)
        out = nodes._web_research(st, [sec], st["settings"])
        self.assertEqual(self.opens, [WEB_A["href"]])       # top-1 by Google rank
        self.assertEqual(len(out["A"]), 1)

    def test_refinement_when_a_section_gets_no_picks(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "google", "web_pages_per_section": 2,
                                "web_refine": True})

        def triage(user, call_no):
            if call_no == 1:
                return {"sections": [{"id": "A", "open": [], "why": "none",
                                      "better_queries": ["night cramps relief"]}]}
            return {"sections": [{"id": "A", "open": [1], "why": "ok", "better_queries": []}]}

        self.use_google({"leg cramps causes": [WEB_A], "night cramps relief": [WEB_B]}, triage=triage)
        out = nodes._web_research(st, [sec], st["settings"])
        self.assertEqual(self.searches, ["leg cramps causes", "night cramps relief"])
        self.assertEqual(self.opens, [WEB_A["href"]])       # the refined pick, rank re-numbered
        self.assertEqual([s["title"] for s in out["A"]], ["Mayo leg cramps"])
        self.assertIn("google:night cramps relief", st["queries_done"])

    def test_refinement_off_falls_back_to_top_k(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "google", "web_pages_per_section": 1,
                                "web_refine": False})

        def triage(user, call_no):
            return {"sections": [{"id": "A", "open": [], "why": "none",
                                  "better_queries": ["night cramps relief"]}]}

        self.use_google({"leg cramps causes": [WEB_A], "night cramps relief": [WEB_B]}, triage=triage)
        out = nodes._web_research(st, [sec], st["settings"])
        self.assertEqual(self.searches, ["leg cramps causes"])    # no refinement search
        self.assertEqual(self.opens, [WEB_A["href"]])             # top-k fallback
        self.assertNotIn("google:night cramps relief", st["queries_done"])
        self.assertEqual(len(out["A"]), 1)

    def test_first_round_google_budget_defers_later_sections(self):
        secs = [section("A", pending={"literature": [], "public": ["q a"]}),
                section("B", pending={"literature": [], "public": ["q b"]})]
        st = state_of(secs, sources={}, focus=["A", "B"], queries_done=[], coverage_round=0,
                      settings={"web_search": "google", "web_pages_per_section": 1,
                                "max_google_queries": 3})
        self.use_google({"q a": [WEB_A], "q b": [WEB_B]})
        values = iter([3, 1, 1, 1])
        with mock.patch("medical_brain.research.sources.google.remaining",
                        side_effect=lambda: next(values)):
            out = nodes._web_research(st, list(secs), st["settings"])
        self.assertEqual(self.searches, ["q a"])                  # B deferred, no Google traffic
        self.assertNotIn("google:q b", st["queries_done"])
        self.assertIn("google:q a", st["queries_done"])
        self.assertEqual(len(out.get("A", [])), 1)

    def test_web_off_makes_no_google_calls(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "off"})
        self.use_google({"leg cramps causes": [WEB_A]})
        self.assertEqual(nodes._web_research(st, [sec], st["settings"]), {})
        self.assertEqual(self.searches, [])

    def test_sites_mode_makes_no_google_calls(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "sites", "web_pages_per_section": 1})
        self.pages[SITE_A["href"]] = {"url": SITE_A["href"], "title": "Mayo site leg cramps",
                                      "html": "<html>A</html>"}
        self.use_sites({"leg cramps causes": [SITE_A]})

        def no_google(*a, **k):
            raise AssertionError("Google must not be searched in sites mode")

        patcher = mock.patch("medical_brain.research.sources.google.search", no_google)
        patcher.start()
        self.addCleanup(patcher.stop)
        out = nodes.source_collector(st)
        self.assertEqual(self.site_searches, ["leg cramps causes"])
        self.assertNotIn("google:leg cramps causes", out["queries_done"])
        self.assertIn("sites:leg cramps causes", out["queries_done"])
        self.assertEqual(self.opens, [SITE_A["href"]])
        self.assertEqual(len(out["sources"]), 1)
        self.assertEqual(out["sources"]["S1"]["url"], SITE_A["href"])

    def test_sites_plus_google_merges_both(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "sites+google", "web_pages_per_section": 2})
        self.pages[SITE_A["href"]] = {"url": SITE_A["href"], "title": "Mayo site leg cramps",
                                      "html": "<html>A</html>"}
        seen = {}

        def triage(user, call_no):
            if not seen:
                seen["user"] = user
            return self.default_triage(user, call_no)

        self.use_both(site_hits={"leg cramps causes": [SITE_A]},
                      google_hits={"leg cramps causes": [WEB_A]}, triage=triage)
        out = nodes.source_collector(st)
        self.assertEqual(self.site_searches, ["leg cramps causes"])
        self.assertEqual(self.searches, ["leg cramps causes"])
        self.assertIn("sites:leg cramps causes", out["queries_done"])
        self.assertIn("google:leg cramps causes", out["queries_done"])
        self.assertEqual(len(out["sources"]), 2)
        # Site results come first, then Google, in the single triage prompt.
        self.assertLess(seen["user"].index(SITE_A["title"]), seen["user"].index(WEB_A["title"]))

    def test_sites_plus_google_dedupes_shared_href(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "sites+google", "web_pages_per_section": 2})
        self.pages[SITE_A["href"]] = {"url": SITE_A["href"], "title": "Mayo site leg cramps",
                                      "html": "<html>A</html>"}
        self.use_both(site_hits={"leg cramps causes": [SITE_A]},
                      google_hits={"leg cramps causes": [dict(SITE_A, title="Google copy")]})
        out = nodes.source_collector(st)
        self.assertEqual(self.opens, [SITE_A["href"]])       # fetched once, not twice
        self.assertEqual(len(out["sources"]), 1)

    def test_sites_plus_google_survives_google_exception(self):
        sec = section("A", pending={"literature": [], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "sites+google", "web_pages_per_section": 1})
        self.pages[SITE_A["href"]] = {"url": SITE_A["href"], "title": "Mayo site leg cramps",
                                      "html": "<html>A</html>"}
        self.use_both(site_hits={"leg cramps causes": [SITE_A]}, google_hits={})

        def boom(*a, **k):
            raise RuntimeError("google exploded")

        patcher = mock.patch("medical_brain.research.sources.google.search", boom)
        patcher.start()
        self.addCleanup(patcher.stop)
        out = nodes.source_collector(st)                     # must not raise
        self.assertEqual(len(out["sources"]), 1)
        self.assertEqual(self.opens, [SITE_A["href"]])

    def test_google_exception_does_not_stop_research(self):
        sec = section("A", pending={"literature": ["af"], "public": ["leg cramps causes"]})
        st = state_of([sec], sources={}, focus=["A"], queries_done=[],
                      settings={"web_search": "google"})

        def boom(query, sites=None, max_results=10):
            raise RuntimeError("google exploded")

        patcher = mock.patch("medical_brain.research.sources.google.search", boom)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch("medical_brain.research.sources.run_query",
                        side_effect=lambda kind, q, per, years: [dict(SOURCE_A)]):
            out = nodes.source_collector(st)                 # must not raise
        self.assertIn("S1", out["sources"])                  # local source still merged
        self.assertEqual(out["sources"]["S1"]["origin"], "PubMed")

    def test_shared_page_fetched_once_for_two_sections(self):
        secs = [section("A", pending={"literature": [], "public": ["q a"]}),
                section("B", pending={"literature": [], "public": ["q b"]})]
        st = state_of(secs, sources={}, focus=["A", "B"], queries_done=[],
                      settings={"web_search": "google", "web_pages_per_section": 1})
        self.use_google({"q a": [WEB_A], "q b": [WEB_A]})    # different sections, same page
        out = nodes.source_collector(st)
        self.assertEqual(self.opens, [WEB_A["href"]])        # fetched once
        self.assertEqual(len(out["sources"]), 1)
        self.assertEqual(out["sources"]["S1"]["sections"], ["A", "B"])


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


class MixedEvidenceVerdictTest(unittest.TestCase):
    def test_benefit_and_no_benefit_is_mixed_and_never_an_option(self):
        from medical_brain.research import dossier
        state = {"classic_checks": [{"item": "Magnesium", "section": "G"}],
                 "complementary": {"applies": True, "why": "", "items": ["Magnesium"]},
                 "sources": {"S1": {"title": "t", "level": 2}, "S2": {"title": "u", "level": 3}},
                 "facts": [
                     {"id": "F1", "section": "G", "kind": "fact", "claim": "a", "status": "verified",
                      "source_ids": ["S1"], "check": "Magnesium", "strength": "no_benefit"},
                     {"id": "F2", "section": "G", "kind": "fact", "claim": "b", "status": "verified",
                      "source_ids": ["S2"], "check": "Magnesium", "strength": "probably_helps"}]}
        rows = dossier.classic_check_table(state)
        self.assertEqual(rows[0]["verdict"], dossier.MIXED_EVIDENCE)
        options, others = dossier.complementary_lists(rows)
        self.assertEqual(options, [])
        self.assertEqual([r["item"] for r in others], ["Magnesium"])

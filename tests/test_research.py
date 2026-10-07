"""The topic → research workflow, offline: fake sources and a scripted fake model."""
import json
import os
import tempfile
import unittest
from unittest import mock

from medical_brain import llm
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

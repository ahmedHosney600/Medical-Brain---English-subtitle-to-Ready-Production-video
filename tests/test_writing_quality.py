"""Writing-quality upgrades: the hook contest, the ending/English-term/comment code
checks, and stripping a polishing pass's metadata + summary wrapper."""
import json
import re
import unittest
from unittest import mock

from medical_brain import llm
from medical_brain.nodes import chapters, quality, script_writing


class CallLLMAsTest(unittest.TestCase):
    def _use_router(self, router):
        old = llm._router
        llm.set_router(router)
        self.addCleanup(llm.set_router, old)

    def test_temporarily_sets_current_node_and_restores_it(self):
        seen = []

        class Router:
            def call(self, system, user, temperature=None, max_tokens=None):
                seen.append(llm.current_node.get())
                return "answer"

        self._use_router(Router())
        self.assertIsNone(llm.current_node.get())
        self.assertEqual(llm.call_llm_as("hook_judge_a", "s", "u"), "answer")
        self.assertEqual(seen, ["hook_judge_a"])
        self.assertIsNone(llm.current_node.get())

    def test_restores_current_node_when_the_call_fails(self):
        class Router:
            def call(self, system, user, temperature=None, max_tokens=None):
                raise RuntimeError("down")

        self._use_router(Router())
        with self.assertRaises(RuntimeError):
            llm.call_llm_as("hook_writer_alt", "s", "u")
        self.assertIsNone(llm.current_node.get())


class FakeLLM:
    """Records every call and answers with fn(*args, **kwargs)."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        return self.fn(*args, **kwargs)


def use(test, module, name, fake):
    patcher = mock.patch.object(module, name, fake)
    patcher.start()
    test.addCleanup(patcher.stop)
    return fake


class FakeRouter:
    def __init__(self, main, alt):
        self.main, self.alt = main, alt

    def model_for(self, node):
        return self.alt if node == "hook_writer_alt" else self.main


def hook_answer(prefix):
    return ("---\n\n## EGYPTIAN HOOK VARIATIONS\n\n"
            f"### HOOK A — Question\n{prefix} A: إنت حاسس بالشد ده غالباً؟\n\n"
            f"### HOOK B — Myth\n{prefix} B: اللي بيقولك إن ده طبيعي غلط.\n\n"
            f"### HOOK C — Story\n{prefix} C: تخيل إنك نايم وفجأة السمانة شدت.\n\n"
            "### RECOMMENDED HOOK\n**Use**: Hook A\n")


MAIN_HOOKS = hook_answer("MAIN")
ALT_HOOKS = hook_answer("ALT-WINNER")


def candidate_texts(user):
    """{label: candidate text} from the judge's user prompt."""
    blocks = re.findall(r"CANDIDATE (H\d+):\n(.*?)(?=\n\n---\n\nCANDIDATE |\n\nScore every|\Z)",
                        user, re.DOTALL)
    return {label: text for label, text in blocks}


def judge_answer(node, system, user, **kwargs):
    scores = {}
    best = None
    for label, text in candidate_texts(user).items():
        score = 9 if "ALT-WINNER" in text else 3
        scores[label] = {"total": score}
        if score == 9:
            best = label
    return ({"scores": scores, "best": best or "H1"}, "raw")


def contest_state(**over):
    state = {"medical_topic": "تقلصات العضلات بالليل", "strategy_plan": "خطة",
             "source_analysis": "تحليل", "video_analysis": "", "seo_primary_keyword": "",
             "dialect_register": "", "code_switching_level": "", "voice_style": "",
             "audience_level": "", "presenter_profile": "", "avoid_list": ""}
    state.update(over)
    return state


class HookContestTest(unittest.TestCase):
    def patch_contest(self, main_spec="deepseek:deepseek-flash",
                      alt_spec="agy:gemini-3.8-flash-medium", llm=None, llm_as=None, judge=None):
        use(self, script_writing, "get_router", lambda: FakeRouter(main_spec, alt_spec))
        self.main_fake = use(self, script_writing, "call_llm", llm or FakeLLM(lambda *a, **k: MAIN_HOOKS))
        self.alt_fake = use(self, script_writing, "call_llm_as",
                            llm_as or FakeLLM(lambda *a, **k: ALT_HOOKS))
        self.judge_fake = use(self, script_writing, "call_llm_json_as", judge or FakeLLM(judge_answer))
        return self.main_fake, self.alt_fake, self.judge_fake

    def test_picks_the_higher_scored_candidate_and_labels_it(self):
        main_fake, alt_fake, judge_fake = self.patch_contest()
        with mock.patch("builtins.print") as printed:
            out = script_writing.hook_writer(contest_state())

        self.assertTrue(out["hook"].startswith("🏆 CONTEST WINNER (score 20)"))  # 10 per judge after scaling
        winner_block = out["hook"].split("### CONTEST SCORES")[0]
        self.assertIn("ALT-WINNER", winner_block)
        self.assertEqual(set(out["hook_contest"]), {f"H{i}" for i in range(1, 7)})
        winner = max(out["hook_contest"], key=lambda label: out["hook_contest"][label]["score"])
        self.assertEqual(out["hook_contest"][winner]["writer"], "agy:gemini-3.8-flash-medium")
        self.assertEqual(out["hook_contest"][winner]["score"], 20)
        self.assertIn("| Hook | Score |", out["hook"])
        # Both blind judges ran, and the alternate writer was addressed by node name.
        self.assertEqual([c["args"][0] for c in judge_fake.calls], ["hook_judge_a", "hook_judge_b"])
        self.assertEqual(alt_fake.calls[0]["args"][0], "hook_writer_alt")
        self.assertIn("winner by agy:gemini-3.8-flash-medium", " ".join(
            str(c.args[0]) for c in printed.call_args_list))

    def test_contest_is_skipped_when_the_alt_route_is_the_main_model(self):
        self.patch_contest(main_spec="deepseek:deepseek-flash",
                           alt_spec="deepseek:deepseek-flash")
        self.alt_fake.fn = lambda *a, **k: self.fail("the alt writer must not run")
        out = script_writing.hook_writer(contest_state())
        self.assertEqual(out["hook"], MAIN_HOOKS)
        self.assertNotIn("hook_contest", out)
        self.assertEqual(self.alt_fake.calls, [])
        self.assertEqual(self.judge_fake.calls, [])

    def test_contest_is_skipped_when_the_router_has_no_model_for(self):
        use(self, script_writing, "get_router", lambda: object())
        main = use(self, script_writing, "call_llm", FakeLLM(lambda *a, **k: MAIN_HOOKS))
        alt = use(self, script_writing, "call_llm_as",
                  FakeLLM(lambda *a, **k: self.fail("no model_for means no contest")))
        out = script_writing.hook_writer(contest_state())
        self.assertEqual(out, {"hook": MAIN_HOOKS})
        self.assertEqual(len(main.calls), 1)
        self.assertEqual(alt.calls, [])

    def test_a_failing_alt_writer_keeps_the_main_hooks(self):
        self.patch_contest(llm_as=FakeLLM(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))))
        with mock.patch("builtins.print") as printed:
            out = script_writing.hook_writer(contest_state())
        self.assertEqual(out, {"hook": MAIN_HOOKS})
        self.assertEqual(self.judge_fake.calls, [])
        self.assertIn("alternate writer failed", " ".join(str(c.args[0]) for c in printed.call_args_list))

    def test_one_failed_judge_is_skipped_and_the_other_still_decides(self):
        def judge(node, system, user, **kwargs):
            if node == "hook_judge_a":
                raise RuntimeError("judge down")
            return judge_answer(node, system, user)

        self.patch_contest(judge=FakeLLM(judge))
        with mock.patch("builtins.print"):
            out = script_writing.hook_writer(contest_state())
        self.assertIn("ALT-WINNER", out["hook"].split("### CONTEST SCORES")[0])
        self.assertEqual(len(out["hook_contest"]), 6)
        winner = max(out["hook_contest"], key=lambda label: out["hook_contest"][label]["score"])
        self.assertEqual(out["hook_contest"][winner]["score"], 10)   # scaled to the judge's best

    def test_both_judges_failing_keeps_the_main_models_recommendation(self):
        self.patch_contest(judge=FakeLLM(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))))
        with mock.patch("builtins.print") as printed:
            out = script_writing.hook_writer(contest_state())
        self.assertEqual(out, {"hook": MAIN_HOOKS, "hook_contest": {}})
        self.assertIn("both judges failed", " ".join(str(c.args[0]) for c in printed.call_args_list))


def words(prefix, n):
    return " ".join(f"{prefix}{i}" for i in range(n))


def critique_prompt(script):
    captured = {}

    def answer(system, user, temperature=None, max_tokens=None, **kwargs):
        captured["user"] = user
        return ({"critique_grade": "A"}, "raw")

    fake = FakeLLM(answer)
    with mock.patch.object(quality, "call_llm_json", fake), mock.patch("builtins.print"):
        quality.self_critique({"refined_script": script, "blueprint": {}, "script_frame": {}})
    return captured["user"]


class EndingCodeCheckTest(unittest.TestCase):
    def test_heavy_last_section_is_flagged_and_ignores_a_trailing_cta(self):
        script = "\n\n".join([
            "=== الفصل الأول ===\n" + words("a", 100),
            "=== الفصل الثاني ===\n" + words("b", 100),
            "=== الفصل الثالث ===\n" + words("c", 400),
            "=== CTA ===\n" + words("d", 900),
        ])
        heavy = chapters.heavy_ending(script)
        self.assertEqual(heavy["title"], "الفصل الثالث")
        self.assertEqual((heavy["words"], heavy["total"]), (400, 600))
        prompt = critique_prompt(script)
        self.assertIn("CODE CHECK — the ending is too heavy: الفصل الثالث has 400 of 600 words", prompt)
        self.assertIn("move the secondary lists earlier or cut repetition", prompt)
        self.assertIn("grade cannot be A", prompt)

    def test_a_light_ending_is_not_flagged(self):
        script = "\n\n".join([
            "=== الفصل الأول ===\n" + words("a", 200),
            "=== الفصل الثاني ===\n" + words("b", 200),
            "=== الفصل الثالث ===\n" + words("c", 100),
        ])
        self.assertIsNone(chapters.heavy_ending(script))
        self.assertNotIn("the ending is too heavy", critique_prompt(script))

    def test_two_sections_are_compared_too(self):
        heavy = "\n\n".join(["=== الأول ===\n" + words("a", 100),
                             "=== الثاني ===\n" + words("b", 300)])
        self.assertIsNotNone(chapters.heavy_ending(heavy))
        light = "\n\n".join(["=== الأول ===\n" + words("a", 300),
                             "=== الثاني ===\n" + words("b", 100)])
        self.assertIsNone(chapters.heavy_ending(light))


class EnglishTermsTest(unittest.TestCase):
    def test_counts_bare_words_and_parenthesised_groups(self):
        script = ("=== الفصل ===\nالحساس الصغير (Golgi Tendon Organ) — فرامل العضلة. "
                  "وكمان (AF) و (ECG) و (MRI) و (CT) و (DNA) و (CBC). بعدين ECG تاني.")
        bare, groups = chapters.english_terms(script)
        self.assertEqual(bare, ["ECG"])
        self.assertEqual(len(groups), 7)
        self.assertIn("Golgi Tendon Organ", groups)

    def test_cues_and_stage_directions_are_not_spoken_terms(self):
        script = ("=== الفصل ===\n[TOP-RIGHT POPUP: Cramp → شد عضلي]\n"
                  "(الكاميرا قريبة، نبرة هادئة)\nالنص العربي كله هنا.")
        self.assertEqual(chapters.english_terms(script), ([], []))

    def test_code_check_names_the_terms_and_caps_the_grade(self):
        script = ("=== الفصل ===\n" + words("ك", 50) + " Golgi Tendon Organ Arrhythmias "
                  "(Cochrane) (ECG) (MRI) (CT) (DNA) (CBC).")
        bare, groups = chapters.english_terms(script)
        self.assertGreater(len(bare), 3)
        self.assertGreater(len(groups), 5)
        prompt = critique_prompt(script)
        self.assertIn("CODE CHECK — 4 English term(s) outside parentheses", prompt)
        self.assertIn("Golgi", prompt)
        self.assertIn("Arrhythmias", prompt)
        self.assertIn("parenthesised English group(s) (limit 5)", prompt)
        self.assertIn("Grade cannot be A while more than 3 bare English terms remain", prompt)


class CommentQuestionCheckTest(unittest.TestCase):
    def test_an_early_comment_question_passes_the_warning_check(self):
        script = ("=== الفصل ===\nاكتبولي في الكومنت: إنت بتشد عليك السمانة وأنت نايم؟ "
                  + words("ك", 200))
        self.assertTrue(chapters.has_early_comment_question(script))
        self.assertNotIn("no comment question", critique_prompt(script))

    def test_a_late_or_missing_comment_question_is_flagged_as_a_warning(self):
        late = "=== الفصل ===\n" + words("ك", 200) + "\nاكتبولي في الكومنت لو حصل معاك."
        self.assertFalse(chapters.has_early_comment_question(late))
        prompt = critique_prompt(late)
        self.assertIn("CODE CHECK (warning) — no comment question", prompt)
        self.assertIn("warning only", prompt)


class StripScriptExtrasTest(unittest.TestCase):
    REFINED = ("SCRIPT: عنوان\nDuration Target: 12 min\nWord Count: 1,600 / 1,600\n"
               "Medical Fidelity Score: 10/10\n\n---\n\n"
               "=== الفصل الأول ===\n[VISUAL NOTE: close-up]\nالجملة الأولى.\n\n"
               "POLISH SUMMARY:\n- Word count: 1600\n- Changes made: none\n")

    def test_keeps_headers_and_cues_and_drops_metadata_and_summary(self):
        out = chapters.strip_script_extras(self.REFINED)
        self.assertTrue(out.startswith("=== الفصل الأول ==="))
        self.assertIn("[VISUAL NOTE: close-up]", out)
        self.assertIn("الجملة الأولى", out)
        for gone in ("SCRIPT:", "Word Count", "Duration Target", "Fidelity Score", "POLISH SUMMARY"):
            self.assertNotIn(gone, out)

    def test_text_without_a_wrapper_is_untouched(self):
        script = "=== الفصل ===\nنص المخاطبة هنا. "
        self.assertEqual(chapters.strip_script_extras(script), script)

    def test_script_refinement_returns_the_stripped_script(self):
        fake = FakeLLM(lambda *a, **k: self.REFINED)
        use(self, script_writing, "call_llm", fake)
        state = {"dialect_warmth_output": "D", "blueprint": {}, "hook": "هوك",
                 "refined_script": "", "strategy_plan": "", "voice_style": "",
                 "mandatory_mentions": "", "quality_revision_count": 0}
        with mock.patch("builtins.print"):
            out = script_writing.script_refinement(state)
        self.assertTrue(out["refined_script"].startswith("=== الفصل الأول ==="))
        self.assertNotIn("POLISH SUMMARY", out["refined_script"])
        self.assertNotIn("Word Count", out["refined_script"])

    def test_truth_verifier_returns_the_stripped_verified_script(self):
        def answer(system, user, **kwargs):
            return ({"truth_score": 10, "truth_pass": True,
                     "truth_verification_report": "ok",
                     "verified_script": self.REFINED}, "raw")

        with mock.patch.object(quality, "call_llm_json", FakeLLM(answer)), \
                mock.patch("builtins.print"):
            out = quality.medical_truth_verifier({"refined_script": self.REFINED,
                                                  "research_dossier": "", "blueprint": {}})
        self.assertTrue(out["refined_script"].startswith("=== الفصل الأول ==="))
        self.assertNotIn("POLISH SUMMARY", out["refined_script"])
        self.assertNotIn("Word Count", out["refined_script"])


if __name__ == "__main__":
    unittest.main()


class EndingSecondToLastTest(unittest.TestCase):
    def test_a_crammed_second_to_last_section_with_a_light_last_one_is_flagged(self):
        # The real deepseek-writes run: act 4 held red flags + medicines + diseases
        # (630 of 1,973 words, 1.9× the average) and act 5 was a short prevention list.
        script = "\n\n".join(["=== S ===\n" + words("s", 73), "=== A1 ===\n" + words("a", 274),
                              "=== A2 ===\n" + words("b", 373), "=== A3 ===\n" + words("c", 457),
                              "=== A4 ===\n" + words("d", 630), "=== A5 ===\n" + words("e", 166)])
        heavy = chapters.heavy_ending(script)
        self.assertEqual(heavy["title"], "A4")


class ReworedDisclaimerTest(unittest.TestCase):
    def test_a_reworded_disclaimer_counts_as_present(self):
        # A real run: the frame said «… مش بديل عن استشارة طبيبك …», the polished script said
        # «الكلام ده توعية وإنقاذ للحظة عارضة، مش بديل عن إن طبيبك يبص على حالتك».
        from medical_brain.nodes import chapters as C
        script = ("=== A ===\nالساعة تلاتة الفجر وسمانتك شدت. الكلام ده توعية، مش بديل عن إن طبيبك يبص على حالتك.\n"
                  + "كلام عن الإطالة والوقاية. " * 30)
        self.assertTrue(C.has_disclaimer(script, "قبل ما نغوص في الحكاية: الخطوات دي هدفها التوعية "
                                                 "وإنقاذ الموقف العارض، مش بديل عن استشارة طبيبك خصوصاً لو بتاخد أدوية"))

    def test_no_disclaimer_is_still_missing(self):
        from medical_brain.nodes import chapters as C
        self.assertFalse(C.has_disclaimer("=== A ===\n" + "كلام عن الإطالة والوقاية. " * 30,
                                          "تنبيه: ده مش بديل عن الدكتور"))


class ChapterMapTest(unittest.TestCase):
    def test_body_writer_sees_the_chapter_proportions(self):
        from medical_brain.nodes import script_writing as SW
        bp = {"target_minutes": 11, "words_per_minute": 135,
              "chapters": [{"id": "C1", "title": "أول", "words": 270}, {"id": "C2", "title": "تاني", "words": 410},
                           {"id": "C3", "title": "تالت", "words": 430}, {"id": "C4", "title": "رابع", "words": 200}]}
        line = SW._chapter_map_line(bp)
        self.assertIn("رابع: about 15% of the body", line)
        self.assertIn("FINAL chapter must stay the lightest", line)
        self.assertEqual(SW._chapter_map_line({"chapters": [{"id": "C1", "words": 300}]}), "")


class MeasuredWordCountTest(unittest.TestCase):
    def test_self_critique_gets_the_measured_word_count(self):
        script = "=== الفصل الأول ===\n" + words("a", 300) + "\n\n=== الفصل الثاني ===\n" + words("b", 200)
        prompt = critique_prompt(script)
        self.assertIn("CODE MEASUREMENTS (exact", prompt)
        self.assertIn("spoken words = 500", prompt)


class JudgeDoesNotRewriteTest(unittest.TestCase):
    def _result(self, judge_model):
        from medical_brain.nodes import quality
        from medical_brain import llm

        class _Router:
            main = "deepseek:deepseek-flash"
            def model_for(self, node):
                return judge_model if node == "self_critique" else self.main

        old = llm._router
        llm._router = _Router()
        try:
            state = {"refined_script": "النص اللي كتبه الكاتب", "truth_score": 9, "truth_pass": True,
                     "fidelity_score": 9, "medical_accuracy_pass": True, "quality_revision_count": 0,
                     "max_quality_revision_count": 4}
            data = {"critique_grade": "B", "dialect_authenticity_score": 9, "warmth_score": 9,
                    "revised_script": "نص الحكم المعاد كتابته"}
            return quality._critique_result(state, "B", data, "raw")
        finally:
            llm._router = old

    def test_another_model_judge_does_not_replace_the_writers_text(self):
        # A Gemini judge rewriting a DeepSeek script round after round made it heavier.
        self.assertEqual(self._result("agy:gemini-3.8-flash-high")["refined_script"], "النص اللي كتبه الكاتب")

    def test_same_model_judge_keeps_the_old_behaviour(self):
        self.assertEqual(self._result("deepseek:deepseek-flash")["refined_script"], "نص الحكم المعاد كتابته")


class BracketHeaderTest(unittest.TestCase):
    def test_bracketed_act_headers_become_chapter_headers(self):
        from medical_brain.nodes import chapters as C
        text = "[HOOK]\nكلام\n[ACT 1 — الحركة الميكانيكية]\n[VISUAL NOTE: لقطة]\nكلام\n[ACT 2: اللغز]\nكلام"
        out = C.normalize_section_headers(text)
        self.assertIn("=== ACT 1 — الحركة الميكانيكية ===", out)
        self.assertIn("=== ACT 2: اللغز ===", out)
        self.assertIn("[HOOK]", out)
        self.assertIn("[VISUAL NOTE: لقطة]", out)


class WholeDialectGrowthGuardTest(unittest.TestCase):
    def test_a_revision_that_grows_too_much_is_asked_again(self):
        current = " ".join(f"ك{i}" for i in range(300))
        grown = " ".join(f"ز{i}" for i in range(420))      # +40%
        tight = " ".join(f"ت{i}" for i in range(310))
        calls = []

        def answer(system, user, temperature=None, max_tokens=None):
            calls.append(user)
            return tight if "YOUR LAST REWRITE WAS" in user else grown

        use(self, script_writing, "call_llm", FakeLLM(answer))
        with mock.patch("builtins.print"):
            out = script_writing.dialect_warmth_layer({"quality_revision_count": 1, "refined_script": current,
                                                       "blueprint": {}, "self_critique_output": "fix x"})
        self.assertEqual(out["dialect_warmth_output"], tight)
        self.assertEqual(sum("YOUR LAST REWRITE WAS" in c for c in calls), 1)


class LongDisclaimerTest(unittest.TestCase):
    def test_a_long_disclaimer_is_flagged_and_a_short_one_is_not(self):
        long_one = ("الكلام ده للتوعية ومش بديل عن طبيبك " + "وأي تعديل لازم يكون تحت إشراف طبي متخصص يناسب حالتك " * 6)
        self.assertIsNotNone(chapters.long_disclaimer("=== A ===\n" + long_one))
        self.assertIsNone(chapters.long_disclaimer("=== A ===\nالكلام ده للتوعية ومش بديل عن طبيبك."))
        self.assertIn("CODE CHECK — the disclaimer is", critique_prompt("=== A ===\n" + long_one + "\n\n" + words("a", 200)))


class RetimeHeaderLabelsTest(unittest.TestCase):
    def test_header_timestamp_rewritten_to_measured_start(self):
        from medical_brain.nodes.chapters import retime_header_labels
        script = ("كلمة " * 135 + "\n=== ACT 1 — بداية ===\n" + "كلمة " * 270
                  + "\n=== OUTRO | Timestamp: 11:35 | Energy: 2/5 ===\nمع السلامة\n")
        out = retime_header_labels(script, 135)
        self.assertIn("=== OUTRO | Timestamp: 3:00 | Energy: 2/5 ===", out)
        self.assertIn("=== ACT 1 — بداية ===", out)
        self.assertEqual(out.replace("3:00", "11:35"), script)


class HookTieBreakTest(unittest.TestCase):
    def test_tie_goes_to_the_main_writer(self):
        from medical_brain.nodes.script_writing import _contest_hook
        cands = [("H1", "agy hook"), ("H2", "deepseek hook")]
        writers = {"H1": "agy:x", "H2": "deepseek:y"}
        hook, _ = _contest_hook(cands, {"H1": 18.89, "H2": 18.89}, writers, prefer="deepseek:y")
        self.assertTrue(hook.split("\n\n")[1] == "deepseek hook")
        hook, _ = _contest_hook(cands, {"H1": 19, "H2": 18.89}, writers, prefer="deepseek:y")
        self.assertTrue(hook.split("\n\n")[1] == "agy hook")


class TimedBracketHeaderTest(unittest.TestCase):
    def test_timed_section_lines_become_headers_and_cues_stay(self):
        from medical_brain.nodes.chapters import normalize_section_headers
        text = ("[HOOK]\nكلام\n[الفخ الأول: مش كل صداع جيوب أنفية | 1:15–3:15 | Energy: 3]\nكلام\n"
                "[VISUAL NOTE: لافتة | 0:30]\n[المقدمة والتعريف | 0:35–1:15 | Energy: 3]\n"
                "[وقفة — حزم]\n[TEXT OVERLAY: الفخ الأول]")
        out = normalize_section_headers(text)
        self.assertIn("=== الفخ الأول: مش كل صداع جيوب أنفية ===", out)
        self.assertIn("=== المقدمة والتعريف ===", out)
        for kept in ("[HOOK]", "[VISUAL NOTE: لافتة | 0:30]", "[وقفة — حزم]", "[TEXT OVERLAY: الفخ الأول]"):
            self.assertIn(kept, out)


class RevisionGrowthTest(unittest.TestCase):
    def test_rewritten_script_only_drops_trailing_notes(self):
        from medical_brain.nodes.script_writing import _rewritten_script_only
        out = ("## DIALECT\n### Register: x\n### Rewritten Script\n**د. أحمد حسني:**\nكلام كتير\n\n"
               "**Remaining flags**:\n- ملاحظة طويلة\n")
        body = _rewritten_script_only(out)
        self.assertIn("كلام كتير", body)
        self.assertIn("**د. أحمد حسني:**", body)
        self.assertNotIn("ملاحظة", body)
        self.assertEqual(_rewritten_script_only("بس سكريبت"), "بس سكريبت")

    def test_single_script_revision_that_grows_is_trimmed_back(self):
        from medical_brain.nodes import script_writing as sw
        old = "=== أ ===\n" + "كلمة " * 1000
        grown = "=== أ ===\n" + "كلمة " * 1400
        trimmed = "=== أ ===\n" + "كلمة " * 1040
        calls = []

        def fake(system, user, **kw):
            calls.append(user)
            return trimmed if user.startswith("TRIM") else grown
        state = {"blueprint": {}, "refined_script": old, "quality_revision_count": 1,
                 "dialect_warmth_output": grown}
        with mock.patch.object(sw, "call_llm", side_effect=fake), \
                mock.patch.object(sw, "repair_script", side_effect=lambda st, s, *a: s), \
                mock.patch("builtins.print"):
            out = sw.script_refinement(state)
        self.assertTrue(any(c.startswith("TRIM") for c in calls))
        self.assertLess(sw.chapters.spoken_words(out["refined_script"]), 1100)

    def test_first_pass_is_not_held_to_the_old_length(self):
        from medical_brain.nodes import script_writing as sw
        grown = "=== أ ===\n" + "كلمة " * 1400
        state = {"blueprint": {}, "refined_script": "", "quality_revision_count": 0,
                 "dialect_warmth_output": grown}
        with mock.patch.object(sw, "call_llm", return_value=grown) as llm, \
                mock.patch.object(sw, "repair_script", side_effect=lambda st, s, *a: s), \
                mock.patch("builtins.print"):
            sw.script_refinement(state)
        self.assertEqual(llm.call_count, 1)


class WrappedHeaderTest(unittest.TestCase):
    def test_bracketed_chapter_with_pipe_inside_equals(self):
        from medical_brain.nodes.chapters import normalize_section_headers, content_sections
        text = ("[HOOK]\nكلام\n=== [CHAPTER 1 | علاج الصداع] ===\nكلام كتير\n"
                "=== [CHAPTER 2 | الجيوب] ===\nكلام تاني\n=== DISCLAIMER | Timestamp: 1:05 ===\nتنبيه\n")
        out = normalize_section_headers(text)
        self.assertIn("=== CHAPTER 1 — علاج الصداع ===", out)
        self.assertIn("=== CHAPTER 2 — الجيوب ===", out)
        self.assertIn("=== DISCLAIMER | Timestamp: 1:05 ===", out)      # a production label stays
        self.assertEqual([t for t, _ in content_sections(out)], ["CHAPTER 1 — علاج الصداع", "CHAPTER 2 — الجيوب"])
        self.assertEqual(normalize_section_headers(out), out)


class WrappedHeaderWithTimesTest(unittest.TestCase):
    def test_times_after_the_brackets_are_dropped(self):
        from medical_brain.nodes.chapters import normalize_section_headers
        out = normalize_section_headers("=== [CHAPTER 1: علاج الشد] | 0:00–3:00 | Energy: 4 ===\nكلام\n")
        self.assertIn("=== CHAPTER 1: علاج الشد ===", out)


class HookLoopTest(unittest.TestCase):
    def test_judges_see_the_planned_hook_loop(self):
        from medical_brain.nodes.script_writing import _hook_judge_user
        state = {"medical_topic": "cramps", "blueprint": {"chapters": [
            {"id": "C1", "opens_loops": ["الحركة الغلط في السرير", "سؤال تاني"]},
            {"id": "C2", "closes_loops": ["الحركة الغلط في السرير"]}]}}
        prompt = _hook_judge_user(state, [("H1", "هوك")])
        self.assertIn("PLANNED HOOK LOOP", prompt)
        self.assertIn("الحركة الغلط في السرير", prompt)
        self.assertIn("plants_loop", prompt)
        self.assertNotIn("PLANNED HOOK LOOP", _hook_judge_user({"medical_topic": "x"}, [("H1", "هوك")]))


class HeaderPipeTimeTest(unittest.TestCase):
    def test_bare_pipe_time_is_retimed_and_dashes_are_not_words(self):
        from medical_brain.nodes.chapters import retime_header_labels
        from medical_brain.exports.workbook import spoken_text
        script = "كلمة " * 135 + "— — —\n=== CTA | 11:00 | Energy: 3/5 ===\nاشترك\n"
        self.assertIn("=== CTA | 1:00 | Energy: 3/5 ===", retime_header_labels(script, 135))
        self.assertEqual(len(spoken_text("كلام — تاني --- و… خلاص").split()), 4)


class LastChapterOverPlanTest(unittest.TestCase):
    def test_final_chapter_far_over_its_plan_is_flagged(self):
        from medical_brain.nodes.quality import _last_chapter_over_plan
        script = "".join(f"=== C{i} ===\n" + "كلمة " * n + "\n" for i, n in enumerate([520, 460, 480, 500], 1))
        bp = {"chapters": [{"words": 420}, {"words": 460}, {"words": 440}, {"words": 310}]}
        found = _last_chapter_over_plan(script, bp)
        self.assertIsNotNone(found)
        self.assertIn("planned 310", found["title"])
        bp["chapters"][-1]["words"] = 450                 # within 1.3× of its plan: fine
        self.assertIsNone(_last_chapter_over_plan(script, bp))


class ShareLineTest(unittest.TestCase):
    BODY = "كلام مفيد عن الصداع. " * 120

    def test_missing_share_line_goes_before_the_goodbye(self):
        from medical_brain.nodes.chapters import ensure_share_line, SHARE_LINE
        script = self.BODY + "\n\nاكتب في التعليقات كام يوم. خلي بالك من نفسك. أشوفك في الحلقة الجاية.\n"
        out, added = ensure_share_line(script)
        self.assertTrue(added)
        self.assertLess(out.find(SHARE_LINE), out.find("أشوفك"))
        self.assertGreater(out.find(SHARE_LINE), out.find("اكتب في التعليقات"))
        self.assertEqual(ensure_share_line(out), (out, False))

    def test_present_or_short_is_left_alone(self):
        from medical_brain.nodes.chapters import ensure_share_line
        has = self.BODY + "\n\nابعت الفيديو ده لأمك. أشوفك في الحلقة الجاية.\n"
        self.assertFalse(ensure_share_line(has)[1])
        self.assertFalse(ensure_share_line("كلام قليل. أشوفك في الحلقة الجاية.")[1])

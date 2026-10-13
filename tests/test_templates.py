"""Ready-made model templates: config contents, --template/--templates CLI, menu choice."""
import builtins
import contextlib
import io
import sys
import unittest
from unittest import mock

from medical_brain import setup_models
from medical_brain.llm.config import JUDGE_ROUTES, MODEL_TEMPLATES, apply_template


BASE = {
    "provider": "main",
    "main_model": "gemini:old-main",
    "single_model": "claude:old-single",
    "backup_model": "gemini:old-backup",
    "hybrid_routes": {"medical_truth_verifier": "claude:old-judge"},
    "providers": {"gemini": {"type": "canvas_proxy"}, "agy": {"type": "agy"}},
    "extra_key": "kept",
}


class ApplyTemplateTest(unittest.TestCase):
    def test_sets_every_field_and_keeps_providers(self):
        for name, template in MODEL_TEMPLATES.items():
            with self.subTest(name=name):
                out = apply_template(BASE, name)
                self.assertEqual(out["provider"], "hybrid")
                self.assertEqual(out["main_model"], template["main_model"])
                self.assertEqual(out["single_model"], template["single_model"])
                self.assertEqual(out["backup_model"], template["backup_model"])
                self.assertEqual(out["hybrid_routes"], template["hybrid_routes"])
                self.assertIs(out["providers"], BASE["providers"])
                self.assertEqual(out["extra_key"], "kept")
        self.assertEqual(BASE["main_model"], "gemini:old-main")   # input untouched

    def test_every_template_routes_all_ten_judges(self):
        self.assertEqual(len(JUDGE_ROUTES), 10)
        for name, template in MODEL_TEMPLATES.items():
            with self.subTest(name=name):
                self.assertEqual(set(template["hybrid_routes"]), set(JUDGE_ROUTES))
                self.assertEqual(set(apply_template(BASE, name)["hybrid_routes"]), set(JUDGE_ROUTES))

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            apply_template(BASE, "no-such-template")


class TemplateCliTest(unittest.TestCase):
    def base_config(self):
        return {**BASE, "providers": dict(BASE["providers"])}

    def patch_config(self):
        return mock.patch.object(setup_models.lp, "load_llm_config", lambda *a, **k: self.base_config())

    def test_template_with_known_name_tests_and_saves(self):
        saved = {}
        tested = []
        with mock.patch.object(sys, "argv", ["setup_models.py", "--template", "gemini"]), \
                self.patch_config(), \
                mock.patch.object(setup_models.lp, "test_model",
                                  lambda spec, providers: (tested.append(spec), (True, "answers (0.0 s)"))[1]), \
                mock.patch.object(setup_models.lp, "save_llm_config",
                                  lambda config, *a, **k: saved.update(config=config)):
            setup_models.main()
        self.assertEqual(saved["config"]["provider"], "hybrid")
        self.assertEqual(saved["config"]["main_model"], MODEL_TEMPLATES["gemini"]["main_model"])
        self.assertEqual(saved["config"]["hybrid_routes"], MODEL_TEMPLATES["gemini"]["hybrid_routes"])
        self.assertEqual(set(tested), set(setup_models.distinct_models(saved["config"])))

    def test_unknown_template_exits_2_and_saves_nothing(self):
        saved = []
        with mock.patch.object(sys, "argv", ["setup_models.py", "--template", "no-such-template"]), \
                self.patch_config(), \
                mock.patch.object(setup_models.lp, "save_llm_config", lambda *a, **k: saved.append(1)):
            with self.assertRaises(SystemExit) as cm:
                setup_models.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(saved, [])

    def test_templates_lists_the_three(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["setup_models.py", "--templates"]), \
                contextlib.redirect_stdout(out):
            setup_models.main()
        text = out.getvalue()
        for name in MODEL_TEMPLATES:
            self.assertIn(name, text)
        self.assertIn("Gemini only", text)
        self.assertIn("(recommended)", text)

    def test_interactive_choice_2_applies_gemini_deepseek(self):
        saved = {}
        answers = iter(["2", "3"])
        with mock.patch.object(sys, "argv", ["setup_models.py"]), \
                mock.patch.object(setup_models, "check_packages", lambda: None), \
                self.patch_config(), \
                mock.patch.object(setup_models, "show_status", lambda config: {}), \
                mock.patch.object(setup_models, "offer_keys", lambda config, status: False), \
                mock.patch.object(setup_models, "collect_models", lambda config, status: []), \
                mock.patch.object(setup_models, "print_models", lambda specs, config: None), \
                mock.patch.object(setup_models.lp, "test_model", lambda spec, providers: (True, "answers (0.0 s)")), \
                mock.patch.object(setup_models.lp, "save_llm_config",
                                  lambda config, *a, **k: saved.update(config=config)), \
                mock.patch.object(builtins, "input", side_effect=lambda prompt="": next(answers)):
            setup_models.main()
        self.assertEqual(saved["config"]["single_model"], "deepseek:deepseek-flash")
        self.assertEqual(saved["config"]["hybrid_routes"], MODEL_TEMPLATES["gemini-deepseek"]["hybrid_routes"])


if __name__ == "__main__":
    unittest.main()

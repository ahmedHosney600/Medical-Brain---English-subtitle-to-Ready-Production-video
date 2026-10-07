"""The package wires together: graph, step registry, prompt files."""
import glob
import os
import re
import unittest

from medical_brain import graph
from medical_brain.paths import PROMPTS_DIR
from medical_brain.prompts import load_prompt


class StructureTest(unittest.TestCase):
    def test_graph_builds_with_every_registered_step(self):
        app = graph.build_app()
        nodes = set(app.get_graph().nodes) - {"__start__", "__end__"}
        self.assertEqual(nodes, {name for name, _ in graph.NODES})
        self.assertEqual(len(graph.NODES), len(nodes), "duplicate step names in NODES")

    def test_every_prompt_file_loads(self):
        files = glob.glob(os.path.join(PROMPTS_DIR, "**", "*.md"), recursive=True)
        self.assertGreater(len(files), 25)
        for path in files:
            name = os.path.relpath(path, PROMPTS_DIR)[:-3]
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            markers = set(re.findall(r"\{\{([A-Z_]+)\}\}", raw))
            text = load_prompt(name, **{m: f"<{m}>" for m in markers})
            self.assertTrue(text.strip(), name)
            self.assertNotIn("{{", text, name)

    def test_missing_marker_is_an_error(self):
        with self.assertRaises(KeyError):
            load_prompt("packaging_generator")   # needs EGYPTIAN_CTR_RULES and FACE_RULES

    def test_each_step_has_its_prompt(self):
        for name, _ in graph.NODES:
            candidates = glob.glob(os.path.join(PROMPTS_DIR, name + "*.md"))
            self.assertTrue(candidates, f"no prompts/{name}.md")


if __name__ == "__main__":
    unittest.main()

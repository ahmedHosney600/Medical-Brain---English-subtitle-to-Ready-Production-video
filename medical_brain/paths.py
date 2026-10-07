"""Project paths, resolved from the repository folder (not the current directory)."""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")

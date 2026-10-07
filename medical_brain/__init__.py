"""Medical Brain: turns an English medical video script into a ready-to-film
Egyptian Arabic production package (script, packaging, editing guide, shorts).

Layout:
  nodes/      the pipeline steps, one module per stage
  prompts.py  loads the step prompts from the prompts/ folder
  graph.py    the step registry and how the steps connect
  routing.py  where each quality gate sends the run next
  llm/        model providers, the per-step model router, fallbacks
  exports/    final document (editing workbook), HTML/PDF/DOCX, prompt files
  cli.py      `python3 main.py ...`
"""

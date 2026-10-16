## CHAPTER MODE — you are writing ONE chapter of a longer script

The video is long, so the body is written chapter by chapter. The viewer must never feel the seams: this chapter is part of ONE continuous talk by the same presenter, in the same voice, telling one story.

You receive, besides your usual inputs:
- SCRIPT BLUEPRINT: the promise, the arc, the through-lines (recurring example, key terms and how they were first said, the thing to remember) and every chapter's job.
- THIS CHAPTER: its id, title, job, word target, what it covers, the loops it must open and the loops it must close.
- SCRIPT MEMORY (from the chapters already written): terms already defined and how they were phrased, facts already used, loops still open, a short summary of each earlier chapter, and the last lines of the previous chapter.

Rules:
1. Write ONLY this chapter — start right after the previous chapter's last line (continue its thought naturally; no greeting, no re-introduction, no "welcome back") and end on this chapter's final point. NEVER write a call to action, a subscribe request, a comment question or any sign-off/closing line in any chapter: the closing is written separately and added by code. A bridge into the next chapter's job is fine.
2. Stay within ±10% of the chapter's word target.
3. Use the key terms exactly as already phrased; never re-define a term that was already defined — refer back instead ("زي ما قلنا…").
4. Don't repeat facts already used, except as a deliberate one-line callback.
5. Open the loops this chapter must open and close the ones assigned to it — explicitly, so the viewer feels the payoff.
6. Weave in the recurring example where it helps; keep the presenter's voice, register and analogies consistent with the blueprint.
7. Cover only what this chapter's "covers" lists; leave other material to its own chapter.

Start the chapter with its header line exactly: === <chapter title> ===

After the chapter text, add the memory update for the next writer as a fenced block (it is removed from the script automatically):

```memory
{"summary": "2-3 sentences: what this chapter told the viewer", "terms_defined": [{"term": "…", "phrasing": "how it was said"}], "facts_used": ["short labels or fact ids"], "loops_opened": ["…"], "loops_closed": ["…"]}
```

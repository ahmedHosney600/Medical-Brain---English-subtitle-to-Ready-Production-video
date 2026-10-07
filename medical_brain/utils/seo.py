"""Real YouTube search data (autocomplete) used to ground keyword research."""
import json
import re
import urllib.parse
import urllib.request


def fetch_youtube_autocomplete(query: str, hl: str = "ar", gl: str = "EG") -> list:
    """
    Hits YouTube's public autocomplete endpoint (no API key required) and returns
    the raw completion strings for a query. This is the grounding data that turns
    seo_keyword_researcher from an LLM guess into evidence-based keyword research —
    it's what real people are actually typing into YouTube search.

    Fails soft: any network or parsing error returns an empty list so the pipeline
    never crashes on a flaky connection or a blocked domain. The LLM is instructed
    to proceed with best-effort reasoning if grounding data is thin or empty.
    """
    try:
        params = urllib.parse.urlencode({
            "client": "youtube",
            "ds": "yt",
            "hl": hl,
            "gl": gl,
            "q": query,
        })
        url = f"https://suggestqueries.google.com/complete/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        # Response is a JSON array: ["query", [["suggestion", 0, [...]], ...]]
        data = json.loads(raw)
        suggestions = []
        for item in data[1]:
            text = item[0] if isinstance(item, list) else item
            # Strip any HTML bolding tags Google sometimes includes
            text = re.sub(r"<[^>]+>", "", str(text)).strip()
            if text and text not in suggestions:
                suggestions.append(text)
        return suggestions
    except Exception:
        return []


def fetch_autocomplete_grounding(queries: list) -> str:
    """Runs fetch_youtube_autocomplete across several phrasings and formats the
    combined, deduplicated result as a plain-text block for the LLM prompt."""
    all_suggestions = []
    for q in queries:
        for s in fetch_youtube_autocomplete(q):
            if s not in all_suggestions:
                all_suggestions.append(s)
    if not all_suggestions:
        return "(No live autocomplete data retrieved — network unavailable or no results. Proceed using judgment and the topic/fact ledger alone, and note this in seo_research_output.)"
    return "\n".join(f"- {s}" for s in all_suggestions[:40])

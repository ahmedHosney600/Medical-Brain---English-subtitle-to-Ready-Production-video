"""Where research facts come from. Literature queries go to PubMed and Europe PMC;
public-health queries go to MedlinePlus and (with a key) trusted web pages."""
from . import europepmc, evidence, http, medlineplus, pubmed, web

LITERATURE = {"PubMed": pubmed.search, "Europe PMC": europepmc.search}
PUBLIC = {"MedlinePlus": medlineplus.search, "Web": web.search}


def enabled() -> list:
    names = list(LITERATURE) + ["MedlinePlus"]
    if web.available():
        names.append(f"Web ({web.available()})")
    return names


def run_query(kind: str, query: str, per_source: int, years: int) -> list:
    """kind: 'literature' or 'public'. Returns source dicts (not yet numbered)."""
    found = []
    for name, fn in (LITERATURE if kind == "literature" else PUBLIC).items():
        try:
            found.extend(fn(query, max_results=per_source, years=years))
        except Exception as e:        # a parser surprise must not stop the research
            http.log.append((name, query, f"{type(e).__name__}: {e}"))
    return found


__all__ = ["europepmc", "evidence", "http", "medlineplus", "pubmed", "web", "enabled", "run_query"]

"""The research workflow graph (topic → verified fact dossier).

    start → topic_planner (or review_planner on a review round)
          → source_collector → source_screener ──gaps──┐ (back to collector)
          → fact_extractor → fact_verifier ──fixes──┐ (back to extractor)
          → coverage_critic ──missing──┐ (back to collector)
          → dossier_editor → end
Every loop has a round limit (settings in research_state['settings'])."""
from langgraph.graph import END, START, StateGraph

from ..llm import track_node
from . import nodes
from .state import ResearchState

NODES = [
    ("topic_planner", nodes.topic_planner),
    ("review_planner", nodes.review_planner),
    ("source_collector", nodes.source_collector),
    ("source_screener", nodes.source_screener),
    ("fact_extractor", nodes.fact_extractor),
    ("fact_verifier", nodes.fact_verifier),
    ("coverage_critic", nodes.coverage_critic),
    ("dossier_editor", nodes.dossier_editor),
]


def route_start(state: dict) -> str:
    return "review_planner" if state.get("review") and state.get("outline") else "topic_planner"


def route_after_review(state: dict) -> str:
    return "source_collector" if state.get("focus") else "dossier_editor"


def route_after_screener(state: dict) -> str:
    return "source_collector" if state.get("need_sources") else "fact_extractor"


def route_after_verifier(state: dict) -> str:
    return "fact_extractor" if state.get("need_fix") else "coverage_critic"


def route_after_coverage(state: dict) -> str:
    return "source_collector" if state.get("need_research") else "dossier_editor"


def build_research_app():
    g = StateGraph(ResearchState)
    for name, fn in NODES:
        g.add_node(name, track_node(name, fn))
    g.add_conditional_edges(START, route_start, {"topic_planner": "topic_planner", "review_planner": "review_planner"})
    g.add_edge("topic_planner", "source_collector")
    g.add_conditional_edges("review_planner", route_after_review,
                            {"source_collector": "source_collector", "dossier_editor": "dossier_editor"})
    g.add_edge("source_collector", "source_screener")
    g.add_conditional_edges("source_screener", route_after_screener,
                            {"source_collector": "source_collector", "fact_extractor": "fact_extractor"})
    g.add_edge("fact_extractor", "fact_verifier")
    g.add_conditional_edges("fact_verifier", route_after_verifier,
                            {"fact_extractor": "fact_extractor", "coverage_critic": "coverage_critic"})
    g.add_conditional_edges("coverage_critic", route_after_coverage,
                            {"source_collector": "source_collector", "dossier_editor": "dossier_editor"})
    g.add_edge("dossier_editor", END)
    return g.compile()

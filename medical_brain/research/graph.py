"""The research workflow graph (topic → verified fact dossier).

    start → expert_brief → topic_planner (or review_planner on a review round)
          → source_collector → source_screener ──gaps──┐ (back to collector)
          → fact_extractor → fact_verifier ──fixes──┐ (back to extractor)
                                 └─ else ─→ fact_merger → coverage_critic ──missing──┐ (back to collector)
                                             → dossier_editor → guide_editor → end
Every loop has a round limit (settings in research_state['settings'])."""
from langgraph.graph import END, START, StateGraph

from ..llm import track_node
from . import nodes
from .state import ResearchState

NODES = [
    ("expert_brief", nodes.expert_brief),
    ("topic_planner", nodes.topic_planner),
    ("review_planner", nodes.review_planner),
    ("source_collector", nodes.source_collector),
    ("source_screener", nodes.source_screener),
    ("fact_extractor", nodes.fact_extractor),
    ("fact_verifier", nodes.fact_verifier),
    ("fact_merger", nodes.fact_merger),
    ("coverage_critic", nodes.coverage_critic),
    ("dossier_editor", nodes.dossier_editor),
    ("guide_editor", nodes.guide_editor),
]


def route_start(state: dict) -> str:
    # A review round has an outline already → plan the changes; a fresh run writes the
    # expert brief first, so the planner gets the specialist's checklist.
    return "review_planner" if state.get("review") and state.get("outline") else "expert_brief"


def route_after_review(state: dict) -> str:
    return "source_collector" if state.get("focus") else "dossier_editor"


def route_after_screener(state: dict) -> str:
    return "source_collector" if state.get("need_sources") else "fact_extractor"


def route_after_verifier(state: dict) -> str:
    return "fact_extractor" if state.get("need_fix") else "fact_merger"


def route_after_coverage(state: dict) -> str:
    return "source_collector" if state.get("need_research") else "dossier_editor"


# The node that always follows another (the static edges of build_research_app).
_STATIC_NEXT = {
    "expert_brief": "topic_planner",
    "topic_planner": "source_collector",
    "source_collector": "source_screener",
    "fact_extractor": "fact_verifier",
    "fact_merger": "coverage_critic",
    "dossier_editor": "guide_editor",
    "guide_editor": END,
}

# Nodes whose next step depends on the state — the same routing functions the edges use.
_CONDITIONAL_NEXT = {
    "review_planner": route_after_review,
    "source_screener": route_after_screener,
    "fact_verifier": route_after_verifier,
    "coverage_critic": route_after_coverage,
}


def next_after(node, state):
    """The node the graph runs after `node` (or END), following its edges. `node` is the
    last completed step, so None (nothing completed yet) gives the graph's first node."""
    if node is None:
        return route_start(state)
    if node in _CONDITIONAL_NEXT:
        return _CONDITIONAL_NEXT[node](state)
    return _STATIC_NEXT.get(node, END)


def build_research_app(entry=None):
    g = StateGraph(ResearchState)
    for name, fn in NODES:
        g.add_node(name, track_node(name, fn))
    if entry:
        # Resume: start exactly at the step that was running when the run stopped.
        g.add_edge(START, entry)
    else:
        g.add_conditional_edges(START, route_start,
                                {"expert_brief": "expert_brief", "review_planner": "review_planner"})
    g.add_edge("expert_brief", "topic_planner")
    g.add_edge("topic_planner", "source_collector")
    g.add_conditional_edges("review_planner", route_after_review,
                            {"source_collector": "source_collector", "dossier_editor": "dossier_editor"})
    g.add_edge("source_collector", "source_screener")
    g.add_conditional_edges("source_screener", route_after_screener,
                            {"source_collector": "source_collector", "fact_extractor": "fact_extractor"})
    g.add_edge("fact_extractor", "fact_verifier")
    g.add_conditional_edges("fact_verifier", route_after_verifier,
                            {"fact_extractor": "fact_extractor", "fact_merger": "fact_merger"})
    g.add_edge("fact_merger", "coverage_critic")
    g.add_conditional_edges("coverage_critic", route_after_coverage,
                            {"source_collector": "source_collector", "dossier_editor": "dossier_editor"})
    g.add_edge("dossier_editor", "guide_editor")
    g.add_edge("guide_editor", END)
    return g.compile()

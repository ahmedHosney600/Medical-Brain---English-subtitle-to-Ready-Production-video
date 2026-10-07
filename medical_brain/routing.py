"""Where the graph goes after each quality gate (revise again, or move on)."""
from langgraph.graph import END

from .state import PipelineState


MAX_TRANSLATION_ITERATIONS = 2

def route_translation_fidelity(state: PipelineState) -> str:
    scores_pass = (
        state.get("naturalness_score", 0) >= 8
        and state.get("contextual_alignment_score", 0) >= 8
        and state.get("translation_grade", "PASS") == "PASS"
    )
    max_iterations = state.get("max_translation_revision_count", MAX_TRANSLATION_ITERATIONS)
    hit_max = state.get("translation_revision_count", 0) >= max_iterations

    if scores_pass or hit_max:
        return "cta_retention_writer"
    else:
        return "body_restructurer"


MAX_QUALITY_ITERATIONS = 2

def route_quality_loop(state: PipelineState) -> str:
    quality_pass = (
        state.get("quality_grade") == "PASS"
        and state.get("truth_pass", False)
        and state.get("truth_score", 0) >= 9
    )
    max_iterations = state.get("max_quality_revision_count", MAX_QUALITY_ITERATIONS)
    hit_max = state.get("quality_revision_count", 0) >= max_iterations

    if quality_pass or hit_max:
        return "packaging_creative_director"
    else:
        return "cta_retention_writer"


MAX_PACKAGING_ITERATIONS = 2

def route_packaging_quality(state: PipelineState) -> str:
    packaging_pass = (state.get("packaging_grade") == "PASS")
    max_iterations = state.get("max_packaging_revision_count", MAX_PACKAGING_ITERATIONS)
    hit_max = state.get("packaging_revision_count", 0) >= max_iterations

    if packaging_pass or hit_max:
        return "transition_designer"
    else:
        return "packaging_generator"


MAX_PRODUCTION_ITERATIONS = 2

def route_production_quality(state: PipelineState) -> str:
    production_pass = (state.get("production_grade") == "PASS")
    max_iterations = state.get("max_production_revision_count", MAX_PRODUCTION_ITERATIONS)
    hit_max = state.get("production_revision_count", 0) >= max_iterations

    if production_pass or hit_max:
        return "final_script_package"
    else:
        return "transition_designer"


MAX_SHORTS_ITERATIONS = 2

def route_shorts_quality(state: PipelineState) -> str:
    shorts_pass = (state.get("shorts_quality_grade") == "PASS")
    max_iterations = state.get("max_shorts_revision_count", MAX_SHORTS_ITERATIONS)
    hit_max = state.get("shorts_revision_count", 0) >= max_iterations

    if shorts_pass or hit_max:
        return END
    else:
        return "shorts_script_extractor"


"""The pipeline graph.

To add a step: write prompts/<step>.md, add the function to the right module in
nodes/, add it to NODES below and connect it in build_app()."""
from langgraph.graph import END, START, StateGraph

from .llm import track_node
from .nodes import analysis, final_package, packaging, production, quality, script_writing, shorts
from .routing import (route_packaging_quality, route_production_quality, route_quality_loop,
                      route_shorts_quality, route_translation_fidelity)
from .state import PipelineState

# name → function, in pipeline order
NODES = [
    ("source_script_analyzer", analysis.source_script_analyzer),
    ("seo_keyword_researcher", analysis.seo_keyword_researcher),
    ("strategy_planner", analysis.strategy_planner),
    ("hook_writer", script_writing.hook_writer),
    ("body_restructurer", script_writing.body_restructurer),
    ("translation_fidelity_auditor", quality.translation_fidelity_auditor),
    ("cta_retention_writer", script_writing.cta_retention_writer),
    ("dialect_warmth_layer", script_writing.dialect_warmth_layer),
    ("fidelity_auditor", quality.fidelity_auditor),
    ("script_refinement", script_writing.script_refinement),
    ("medical_truth_verifier", quality.medical_truth_verifier),
    ("self_critique", quality.self_critique),
    ("packaging_creative_director", packaging.packaging_creative_director),
    ("packaging_generator", packaging.packaging_generator),
    ("packaging_honesty_ctr_auditor", packaging.packaging_honesty_ctr_auditor),
    ("transition_designer", production.transition_designer),
    ("text_animation_overlay_designer", production.text_animation_overlay_designer),
    ("broll_prompt_generator", production.broll_prompt_generator),
    ("production_quality_critique", production.production_quality_critique),
    ("final_script_package", final_package.final_script_package),
    ("shorts_moment_identifier", shorts.shorts_moment_identifier),
    ("shorts_script_extractor", shorts.shorts_script_extractor),
    ("shorts_caption_packager", shorts.shorts_caption_packager),
    ("shorts_quality_gate", shorts.shorts_quality_gate),
]


def build_app():
    workflow = StateGraph(PipelineState)
    for name, fn in NODES:
        # track_node lets the model router know which step is calling
        workflow.add_node(name, track_node(name, fn))

    workflow.add_edge(START, "source_script_analyzer")
    workflow.add_edge("source_script_analyzer", "seo_keyword_researcher")
    workflow.add_edge("seo_keyword_researcher", "strategy_planner")
    workflow.add_edge("strategy_planner", "hook_writer")
    workflow.add_edge("hook_writer", "body_restructurer")
    workflow.add_edge("body_restructurer", "translation_fidelity_auditor")
    workflow.add_conditional_edges(
        "translation_fidelity_auditor",
        route_translation_fidelity,
        {
            "cta_retention_writer": "cta_retention_writer",
            "body_restructurer": "body_restructurer",
        }
    )
    workflow.add_edge("cta_retention_writer", "dialect_warmth_layer")
    workflow.add_edge("dialect_warmth_layer", "fidelity_auditor")
    workflow.add_edge("fidelity_auditor", "script_refinement")
    workflow.add_edge("script_refinement", "medical_truth_verifier")
    workflow.add_edge("medical_truth_verifier", "self_critique")
    workflow.add_conditional_edges(
        "self_critique",
        route_quality_loop,
        {
            "packaging_creative_director": "packaging_creative_director",
            "cta_retention_writer": "cta_retention_writer",
        }
    )
    workflow.add_edge("packaging_creative_director", "packaging_generator")
    workflow.add_edge("packaging_generator", "packaging_honesty_ctr_auditor")
    workflow.add_conditional_edges(
        "packaging_honesty_ctr_auditor",
        route_packaging_quality,
        {
            "transition_designer": "transition_designer",
            "packaging_generator": "packaging_generator",
        }
    )
    workflow.add_edge("transition_designer", "text_animation_overlay_designer")
    workflow.add_edge("text_animation_overlay_designer", "broll_prompt_generator")
    workflow.add_edge("broll_prompt_generator", "production_quality_critique")
    workflow.add_conditional_edges(
        "production_quality_critique",
        route_production_quality,
        {
            "final_script_package": "final_script_package",
            "transition_designer": "transition_designer",
        }
    )
    workflow.add_edge("final_script_package", "shorts_moment_identifier")
    workflow.add_edge("shorts_moment_identifier", "shorts_script_extractor")
    workflow.add_edge("shorts_script_extractor", "shorts_caption_packager")
    workflow.add_edge("shorts_caption_packager", "shorts_quality_gate")
    workflow.add_conditional_edges(
        "shorts_quality_gate",
        route_shorts_quality,
        {
            END: END,
            "shorts_script_extractor": "shorts_script_extractor",
        }
    )

    return workflow.compile()

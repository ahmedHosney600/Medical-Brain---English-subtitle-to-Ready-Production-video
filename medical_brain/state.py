"""The pipeline state shared by all steps, and the defaults a new run starts from."""
from typing import TypedDict


class PipelineState(TypedDict):
    # Input fields
    original_script: str
    source_format: str
    medical_topic: str
    target_duration: str
    target_platform: str
    content_style: str
    dialect_register: str
    code_switching_level: str
    voice_style: str
    audience_level: str
    delivery_format: str
    medical_disclaimer_requirements: str
    cta_goal: str
    broll_availability: str
    mandatory_mentions: str
    reference_egyptian_channels: str
    avoid_list: str
    sensitive_handling: str
    presenter_profile: str
    creator_profile: str
    video_analysis: str
    
    max_translation_revision_count: int
    max_quality_revision_count: int

    # Upstream node outputs
    source_analysis: str
    strategy_plan: str
    hook: str

    # SEO Keyword Research (grounds packaging in real search data)
    seo_primary_keyword: str
    seo_secondary_keywords: str
    seo_search_intent: str
    seo_research_output: str

    # Packaging Loop state (Loop 5 — titles, thumbnails, honesty/CTR gate)
    packaging_brainstorm_output: str
    title_options: str
    thumbnail_concepts: str
    packaging_critique_output: str
    recommended_title: str
    packaging_ab_test_set: str
    thumbnail_face_rules: str
    packaging_grade: str
    packaging_revision_count: int
    max_packaging_revision_count: int

    # Translation Fidelity Loop state
    translation_grade: str
    translation_report: str
    revised_body: str
    naturalness_score: int
    contextual_alignment_score: int
    translation_revision_count: int

    # Downstream node outputs
    cta_output: str
    dialect_warmth_output: str
    fidelity_audit_output: str
    refined_script: str
    self_critique_output: str
    quality_grade: str
    quality_revision_count: int
    final_package_issues: list       # audit problems the final package still has
    best_script: str                 # best round of the quality loop so far (verified script)
    best_script_rank: list
    best_script_scores: dict
    best_script_round: int
    final_package: str
    dialect_score: int
    warmth_score: int
    fidelity_score: int
    medical_accuracy_pass: bool
    disclaimer_check: str
    truth_verification_report: str
    truth_score: int
    truth_pass: bool

    # Post-Production Editing Layers (Loop 3)
    transition_design: str
    text_animation_overlay: str
    broll_prompts: str
    production_critique_output: str
    production_grade: str
    production_revision_count: int
    max_production_revision_count: int

    # Reels/Shorts Extraction Pipeline (Loop 4)
    shorts_moments: str
    shorts_scripts: str
    shorts_captions: str
    shorts_quality_output: str
    shorts_quality_grade: str
    shorts_revision_count: int
    max_shorts_revision_count: int


def apply_defaults(state: dict) -> dict:
    """Fills every field a step may read, so a new run never hits a missing key or None."""
    # Initialize unprovided required fields to prevent KeyError/None issues in prompts
    default_string_fields = [
        "source_format", "medical_topic", "target_duration", "target_platform",
        "medical_disclaimer_requirements", "cta_goal", "reference_egyptian_channels",
        "creator_profile", "video_analysis", "revised_body", "self_critique_output", "production_critique_output",
        "transition_design", "text_animation_overlay", "broll_prompts", "disclaimer_check",
        "shorts_moments", "shorts_scripts", "shorts_captions", "shorts_quality_output", "shorts_quality_grade",
        "seo_primary_keyword", "seo_secondary_keywords", "seo_search_intent", "seo_research_output",
        "packaging_brainstorm_output", "title_options", "thumbnail_concepts",
        "packaging_critique_output", "packaging_grade",
        "recommended_title", "packaging_ab_test_set", "thumbnail_face_rules",
    ]
    for field in default_string_fields:
        if field not in state:
            state[field] = ""

    # Bug fix: explicitly initialize all integer counters so nodes never see None
    default_int_fields = {
        "translation_revision_count": 0,
        "quality_revision_count": 0,
        "production_revision_count": 0,
        "shorts_revision_count": 0,
        "naturalness_score": 0,
        "contextual_alignment_score": 0,
        "dialect_score": 0,
        "warmth_score": 0,
        "fidelity_score": 0,
        "max_translation_revision_count": 2,
        "max_quality_revision_count": 2,
        "max_production_revision_count": 2,
        "max_shorts_revision_count": 2,
        "packaging_revision_count": 0,
        "max_packaging_revision_count": 2,
    }
    for field, default in default_int_fields.items():
        if field not in state:
            state[field] = default

    default_bool_fields = {"medical_accuracy_pass": False}
    for field, default in default_bool_fields.items():
        if field not in state:
            state[field] = default
    return state

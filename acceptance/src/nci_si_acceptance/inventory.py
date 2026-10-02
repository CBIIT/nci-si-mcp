"""The required tools, by group: MCP API Specification §2 (A), §3 (B), §4 (C) and §8.4 (W)."""

GROUPS = {
    "A": (
        "resolve_release",
        "get_concept",
        "get_concepts",
        "search_concepts",
        "get_concept_hierarchy",
        "expand_value_set",
        "get_concept_neighborhood",
        "get_concept_subsets",
        "get_concept_mappings",
        "resolve_retired_code",
        "list_relationships",
        "list_terminologies",
    ),
    "B": (
        "resolve_registry_release",
        "get_data_element",
        "search_data_elements",
        "match_data_elements",
        "match_value_meanings",
        "get_form",
        "get_permissible_value",
        "get_code_map",
        "list_contexts",
        "list_classification_schemes",
    ),
    "C": (
        "find_data_elements_for_concept",
        "get_concept_for_permissible_value",
        "resolve_stored_value",
        "get_release_alignment",
    ),
    "W": ("ground_value", "expand_cohort", "harmonize_data_dictionary"),
}

REQUIRED_TOOLS = {name: group for group, names in GROUPS.items() for name in names}

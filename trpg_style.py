"""Shared prompt-only TRPG art direction; no identity traits or model changes."""

TRPG_STYLE_PRESET = (
    "Default TRPG art direction (only when no different art style is explicitly requested): "
    "high-quality delicate semi-realistic 2D anime illustration, fine intricate refined linework, "
    "natural facial proportions, finely rendered character detail, soft coloring with natural color transitions, "
    "natural painterly shading and dimensional lighting, delicate textures in hair, irises and fabric. "
    "By default avoid photorealistic imagery, 3D render, realistic game character rendering, "
    "flat cel shading, thick black outlines and rough sketch textures. "
    "An explicitly requested art style in the request takes priority over this entire default, "
    "including its exclusions and any reference-image style preservation. "
    "This default specifies rendering only; it does not specify character appearance."
)


def apply_trpg_style(prompt):
    return TRPG_STYLE_PRESET + "\nRequest: " + prompt.strip()

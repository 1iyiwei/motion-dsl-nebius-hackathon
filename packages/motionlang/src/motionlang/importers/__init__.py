"""Turn external artwork into MotionLang assets (see SPEC.md §3, `assets`)."""

from .svg import import_svg

__all__ = ["import_svg"]

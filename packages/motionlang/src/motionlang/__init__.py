"""Motion: a declarative, verifiable 2D motion DSL designed for LLM generation."""

from .bake import Baked, bake
from .edits import apply_edits
from .issues import Issue, Report
from .model import SPEC_VERSION, Document
from .parse import parse
from .timing import schedule
from .validate import Result, check, validate
from .verbs import Verb, VerbParams, load_plugins, register

__version__ = "0.1.0"

__all__ = [
    "SPEC_VERSION", "Baked", "Document", "Issue", "Report", "Result", "Verb", "VerbParams",
    "apply_edits", "bake", "check", "load_plugins", "parse", "register", "schedule", "validate",
]

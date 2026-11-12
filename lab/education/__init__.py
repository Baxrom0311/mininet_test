"""Cisco NetAcad Style Automated Lab Grader & Rubric Scoring Engine."""

from .grader import (
    AutoGraderEngine,
    CheckResult,
    ChecklistItem,
    GradeReport,
    LAB_RUBRICS,
    NetAcadGrader,
    auto_grader,
)
from .lab_templates import (
    LAB_TEMPLATES,
    get_lab_template,
    list_available_labs,
)

__all__ = [
    "ChecklistItem",
    "CheckResult",
    "GradeReport",
    "NetAcadGrader",
    "AutoGraderEngine",
    "LAB_RUBRICS",
    "LAB_TEMPLATES",
    "auto_grader",
    "list_available_labs",
    "get_lab_template",
]

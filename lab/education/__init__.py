"""Cisco NetAcad Style Automated Lab Grader & Rubric Scoring Engine."""

from .grader import (
    ChecklistItem,
    GradeReport,
    NetAcadGrader,
    LAB_RUBRICS,
    auto_grader,
    list_available_labs,
    get_lab_template,
)

AutoGraderEngine = NetAcadGrader

__all__ = [
    "ChecklistItem",
    "GradeReport",
    "NetAcadGrader",
    "AutoGraderEngine",
    "LAB_RUBRICS",
    "auto_grader",
    "list_available_labs",
    "get_lab_template",
]

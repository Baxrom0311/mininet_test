"""Network Digital Twin and Predictive Optimization Package."""

from .what_if_analyzer import (
    FailureType,
    PREDEFINED_SCENARIOS,
    RiskLevel,
    TopologyGraph,
    TopologyLink,
    TopologyNode,
    TrafficFlow,
    WhatIfAnalyzer,
    WhatIfReport,
    what_if_analyzer,
)

__all__ = [
    "FailureType",
    "RiskLevel",
    "TopologyNode",
    "TopologyLink",
    "TrafficFlow",
    "WhatIfReport",
    "TopologyGraph",
    "WhatIfAnalyzer",
    "PREDEFINED_SCENARIOS",
    "what_if_analyzer",
]

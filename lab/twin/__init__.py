"""Network Digital Twin and Predictive Optimization Package."""

from .what_if_analyzer import (
    FailureType,
    RiskLevel,
    TopologyNode,
    TopologyLink,
    TrafficFlow,
    WhatIfReport,
    TopologyGraph,
    WhatIfAnalyzer,
)

what_if_analyzer = WhatIfAnalyzer()

__all__ = [
    "FailureType",
    "RiskLevel",
    "TopologyNode",
    "TopologyLink",
    "TrafficFlow",
    "WhatIfReport",
    "TopologyGraph",
    "WhatIfAnalyzer",
    "what_if_analyzer",
]

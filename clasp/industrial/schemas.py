# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
clasp/industrial/schemas.py
============================
Industrial-specific Pydantic schemas for Clasp.

Extends the vendored silex schemas WITHOUT modifying the vendored code.
All Clasp industrial code imports from here, not from vendor.silex.models.schemas.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Industrial Node Types
# These are string values passed as node_type to KnowledgeNode.
# We use str Enum so they compare correctly to the stored strings in SQLite.
# ---------------------------------------------------------------------------

class IndustrialNodeType(str, Enum):
    """Types of nodes in the industrial causal graph."""
    PROCESS_VARIABLE = "ProcessVariable"   # measured sensor value (temp, pressure, flow)
    EQUIPMENT_UNIT   = "EquipmentUnit"     # physical device (valve, pump, reactor)
    ALARM_EVENT      = "AlarmEvent"        # triggered alarm or fault code
    OPERATOR_ACTION  = "OperatorAction"    # human action (opened valve, changed setpoint)
    QUALITY_METRIC   = "QualityMetric"     # product quality (yield, purity, viscosity)


# ---------------------------------------------------------------------------
# Industrial Edge Types
# These are string values passed as edge_type to CausalEdge.
# ---------------------------------------------------------------------------

class IndustrialEdgeType(str, Enum):
    """Types of causal edges in the industrial graph."""
    CAUSES_WITH_LAG = "causes_with_lag"   # A causes B after lag_seconds (main edge type)
    PART_OF         = "part_of"            # this sensor is mounted on this equipment unit
    PRECEDES        = "precedes"           # temporal ordering without full causal certainty


# ---------------------------------------------------------------------------
# Clasp-specific Data Models
# ---------------------------------------------------------------------------

class SensorObservation(BaseModel):
    """
    A single time-series reading from a plant sensor or variable.
    This is the unit of data that flows from ingestion into the engine.
    """
    node_id: str = Field(description="The Clasp node ID this reading belongs to")
    value: float = Field(description="The measured value")
    timestamp: float = Field(
        default_factory=time.time,
        description="Unix timestamp (seconds) of this reading"
    )
    unit: str = Field(default="", description="Engineering unit (e.g. degC, bar, kg/h)")


class CausalPattern(BaseModel):
    """
    A discovered causal pattern between two plant variables.
    Created and maintained by CausalLearner. Stored in-memory during runtime.
    When occurrences >= min_occurrences AND confidence >= min_confidence,
    a CausalEdge is written to the KnowledgeGraph.
    """
    precursor_node: str = Field(description="Node ID of the variable that changes first (cause)")
    outcome_node: str = Field(description="Node ID of the variable that changes after (effect)")
    lag_seconds: float = Field(description="Average observed lag from cause change to effect change")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Fraction of precursor events that were followed by an effect event"
    )
    occurrences: int = Field(
        default=0,
        description="How many times this cause -> effect sequence has been observed"
    )
    total_precursor_events: int = Field(
        default=0,
        description="Total precursor events used to compute confidence"
    )


class Alert(BaseModel):
    """
    A predictive alert fired by WatcherAgent when it detects active precursor patterns.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str = Field(default="PRECURSOR_DETECTED")
    outcome_risk: str = Field(
        description="Description of the outcome being risked (e.g. 'XMEAS_35 Product Quality Drop')"
    )
    pattern: str = Field(
        description="Human-readable description of the precursor chain, e.g. 'A -> B -> C'"
    )
    estimated_time_to_impact: float = Field(
        description="Estimated seconds until the outcome node is affected"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: float = Field(default_factory=time.time)
    triggering_observations: list[str] = Field(
        default_factory=list,
        description="Node IDs of the observations that activated this alert"
    )


class RootCauseStep(BaseModel):
    """A single step in a root cause chain (cause → effect with timing)."""
    node_id: str
    node_label: str
    value: float | None = None
    timestamp: float | None = None
    lag_seconds: float | None = None
    confidence: float | None = None


class RootCauseResult(BaseModel):
    """The structured output of a root cause investigation."""
    affected_node: str = Field(description="The node ID where the problem was observed")
    event_time: float
    chain: list[RootCauseStep] = Field(
        description="Ordered list from root cause (first) to observed effect (last)"
    )
    explanation: str = Field(
        default="",
        description="LLM-generated plain-language explanation for a plant operator"
    )
    analysis_duration_ms: float = Field(default=0.0)


class GraphStats(BaseModel):
    """Statistics about the current state of the industrial causal graph."""
    total_nodes: int
    total_edges: int
    causes_with_lag_edges: int = Field(description="Number of confirmed causal edges")
    node_types: dict[str, int] = Field(default_factory=dict)
    edge_types: dict[str, int] = Field(default_factory=dict)
    isolated_nodes: int = Field(default=0)

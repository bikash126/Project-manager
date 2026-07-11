"""Estimator agent: MVP stories + KB calibration -> WBS, points, risks, confidence.

Mid tier. Uncalibrated out of the box by design — the KB calibration input is
a placeholder until Phase 6 starts feeding estimates-vs-actuals back in.
"""

from __future__ import annotations

from pm_system.agents.base import Agent

WBS_SCHEMA = {
    "type": "object",
    "required": ["wbs_items", "risk_register", "total_points"],
    "additionalProperties": False,
    "properties": {
        "wbs_items": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "story_id", "description", "estimate_points", "confidence"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^WBS-\\d{3}$"},
                    "story_id": {"type": "string", "pattern": "^US-\\d{3}$"},
                    "description": {"type": "string", "minLength": 1},
                    "estimate_points": {"type": "integer", "minimum": 1},
                    "confidence": {
                        "type": "object",
                        "required": ["low", "high"],
                        "additionalProperties": False,
                        "properties": {
                            "low": {"type": "integer", "minimum": 1},
                            "high": {"type": "integer", "minimum": 1},
                        },
                    },
                },
            },
        },
        "risk_register": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description", "likelihood", "impact", "mitigation"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^RISK-\\d{3}$"},
                    "description": {"type": "string", "minLength": 1},
                    "likelihood": {"enum": ["low", "medium", "high"]},
                    "impact": {"enum": ["low", "medium", "high"]},
                    "mitigation": {"type": "string", "minLength": 1},
                },
            },
        },
        "total_points": {"type": "integer", "minimum": 1},
    },
}


class EstimatorAgent(Agent):
    role = "estimator"
    output_schema = WBS_SCHEMA

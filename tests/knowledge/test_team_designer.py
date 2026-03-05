"""Tests for team designer."""

import json

from kodo.knowledge.models import PatternType, QuestionType
from kodo.knowledge.team_designer import _parse_team_design


class TestParseTeamDesign:
    def test_valid_json(self):
        raw = json.dumps({
            "question_type": "research",
            "pattern": "exploration",
            "rationale": "Research question benefits from parallel exploration",
            "roles": [
                {
                    "name": "explorer_market",
                    "system_prompt": "You research market trends",
                    "model_preference": "search",
                    "tools": ["web_search"],
                },
                {
                    "name": "synthesizer",
                    "system_prompt": "You merge findings into coherent analysis",
                    "model_preference": "best",
                    "tools": ["read_artifact", "write_artifact"],
                },
            ],
        })
        design = _parse_team_design(raw)
        assert design.question_type == QuestionType.RESEARCH
        assert design.pattern == PatternType.EXPLORATION
        assert len(design.roles) == 2
        assert design.roles[0].name == "explorer_market"
        assert design.roles[1].model_preference == "best"

    def test_json_with_code_fences(self):
        inner = json.dumps({
            "question_type": "creative",
            "pattern": "deepening",
            "rationale": "Writing task",
            "roles": [
                {
                    "name": "writer",
                    "system_prompt": "You write blog posts",
                },
            ],
        })
        raw = f"```json\n{inner}\n```"
        design = _parse_team_design(raw)
        assert design.question_type == QuestionType.CREATIVE
        assert design.roles[0].name == "writer"
        # Default model_preference should be "best"
        assert design.roles[0].model_preference == "best"

    def test_missing_optional_fields(self):
        raw = json.dumps({
            "question_type": "analysis",
            "pattern": "adversarial",
            "roles": [
                {"name": "advocate", "system_prompt": "Build the case"},
                {"name": "skeptic", "system_prompt": "Attack the case"},
            ],
        })
        design = _parse_team_design(raw)
        assert design.rationale == ""
        assert design.roles[0].tools == []

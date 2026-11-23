"""AI classification and recommendation of findings (masked values only), validated with pydantic."""

import json
import os
from enum import Enum
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from data_guardian.scanner import Finding, mask_value

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 4096
BATCH_SIZE = 50  # findings per API call: the answer costs ~36 tokens each, so 50 stays well under MAX_TOKENS


class ClassifierError(Exception):
    """Raised when the AI step fails. Messages never contain sensitive values."""


class Risk(str, Enum):
    """Risk level of a finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Action(str, Enum):
    """Recommended action for a finding."""

    ANONYMIZE = "anonymize"
    REVIEW = "review"
    KEEP = "keep"


class Classification(BaseModel):
    """Risk classification of one finding (id = index in the findings list)."""

    id: int
    risk: Risk
    reason: str


class Recommendation(BaseModel):
    """Recommended action for one finding."""

    id: int
    action: Action
    reason: str


class ClassificationResult(BaseModel):
    """Validated tool output of the classify step."""

    items: list[Classification]


class RecommendationResult(BaseModel):
    """Validated tool output of the recommend step."""

    items: list[Recommendation]


CLASSIFY_TOOL: dict[str, Any] = {
    "name": "report_classification",
    "description": "Report the risk level of every finding.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "risk": {"type": "string", "enum": [r.value for r in Risk]},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "risk", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}

RECOMMEND_TOOL: dict[str, Any] = {
    "name": "report_recommendations",
    "description": "Report the recommended action for every finding.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "action": {"type": "string", "enum": [a.value for a in Action]},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "action", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}

CLASSIFY_SYSTEM = (
    "You assess data-privacy risk. You receive findings from a file scan. Values are masked "
    "(only the first 2 and last 2 characters are visible) and you never see the real data. "
    "Judge risk from the kind of data and the visible hints. You must call the "
    "report_classification tool exactly once, with one item per finding id."
)

RECOMMEND_SYSTEM = (
    "You advise on handling sensitive data found in a file. You receive findings with a kind, "
    "a masked value and a risk level. For each one choose: anonymize (replace it), review (a "
    "human should check it) or keep (not sensitive enough to remove). You must call the "
    "report_recommendations tool exactly once, with one item per finding id."
)


def _items_payload(findings: list[Finding], extra: dict[int, dict[str, str]] | None = None) -> str:
    """Build the JSON sent to the API: id, kind, masked value and line only."""
    items = []
    for index, finding in enumerate(findings):
        item: dict[str, Any] = {
            "id": index,
            "kind": finding.kind.value,
            "masked": mask_value(finding.value),
            "line": finding.line,
        }
        item.update((extra or {}).get(index, {}))
        items.append(item)
    return json.dumps(items)


def _call_tool(client: anthropic.Anthropic, system: str, tool: dict[str, Any], payload: str) -> dict[str, Any]:
    """Make one API call and return the input of the requested tool call."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[tool],
            tool_choice={"type": "auto"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": payload}],
        )
    except anthropic.APIStatusError as exc:
        raise ClassifierError(f"Claude API request failed (HTTP {exc.status_code}).") from None
    except anthropic.APIConnectionError:
        if any(ch.isspace() for ch in os.environ.get("ANTHROPIC_API_KEY", "")):
            raise ClassifierError(
                "ANTHROPIC_API_KEY contains spaces or line breaks. Re-copy the key without them."
            ) from None
        raise ClassifierError("Could not connect to the Claude API. Check your network.") from None
    except TypeError as exc:
        if "authentication method" not in str(exc):
            raise
        raise ClassifierError("Claude API credentials are missing. Set ANTHROPIC_API_KEY.") from None
    if response.stop_reason == "refusal":
        raise ClassifierError("The model declined the request.")
    if response.stop_reason == "max_tokens":
        raise ClassifierError("The model response was cut off because it was too long. Try a smaller file.")
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return dict(block.input)
    raise ClassifierError(f"The model did not call the {tool['name']} tool.")


def _check_ids(ids: list[int], expected: int) -> None:
    """Make sure the model answered exactly once for every finding."""
    if sorted(ids) != list(range(expected)):
        raise ClassifierError("The model response does not match the findings.")


def classify_findings(findings: list[Finding], client: anthropic.Anthropic | None = None) -> list[Classification]:
    """Classify the risk of each finding. Only masked values are sent to the API."""
    if not findings:
        return []
    client = client or anthropic.Anthropic()
    classifications: list[Classification] = []
    for start in range(0, len(findings), BATCH_SIZE):
        batch = findings[start : start + BATCH_SIZE]
        raw = _call_tool(client, CLASSIFY_SYSTEM, CLASSIFY_TOOL, _items_payload(batch))
        try:
            result = ClassificationResult.model_validate(raw)
        except ValidationError:
            raise ClassifierError("The model returned an invalid classification.") from None
        _check_ids([c.id for c in result.items], len(batch))
        for c in sorted(result.items, key=lambda c: c.id):  # ids are local to the batch: shift them back
            classifications.append(c.model_copy(update={"id": c.id + start}))
    return classifications


def recommend_actions(
    findings: list[Finding],
    classifications: list[Classification],
    client: anthropic.Anthropic | None = None,
) -> list[Recommendation]:
    """Recommend an action for each finding. Only masked values are sent to the API."""
    if not findings:
        return []
    _check_ids([c.id for c in classifications], len(findings))
    client = client or anthropic.Anthropic()
    risks = {c.id: {"risk": c.risk.value} for c in classifications}
    recommendations: list[Recommendation] = []
    for start in range(0, len(findings), BATCH_SIZE):
        batch = findings[start : start + BATCH_SIZE]
        batch_risks = {i: risks[start + i] for i in range(len(batch))}
        raw = _call_tool(client, RECOMMEND_SYSTEM, RECOMMEND_TOOL, _items_payload(batch, batch_risks))
        try:
            result = RecommendationResult.model_validate(raw)
        except ValidationError:
            raise ClassifierError("The model returned an invalid recommendation.") from None
        _check_ids([r.id for r in result.items], len(batch))
        for r in sorted(result.items, key=lambda r: r.id):
            recommendations.append(r.model_copy(update={"id": r.id + start}))
    return recommendations

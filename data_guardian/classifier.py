"""Clasificación y recomendación con IA de los hallazgos (solo valores enmascarados), validadas con pydantic."""

import json
import os
from enum import Enum
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from data_guardian.scanner import Finding, mask_value
from data_guardian.usage import record_response_usage

DEFAULT_CLASSIFIER_MODEL = "claude-haiku-5-5"  # clasificar y recomendar son tareas simples: un modelo más barato alcanza
DEFAULT_AUDIT_MODEL = "claude-sonnet-5-5"  # la auditoría es el control de seguridad: se queda con el modelo más capaz
ENV_CLASSIFIER_MODEL = "CLASSIFIER_MODEL"  # variables opcionales para cambiar los modelos sin tocar el código
ENV_AUDIT_MODEL = "AUDIT_MODEL"
MAX_TOKENS = 4096
BATCH_SIZE = 50  # hallazgos por llamada a la API: la respuesta cuesta ~36 tokens cada uno, así que 50 queda muy por debajo de MAX_TOKENS


class ClassifierError(Exception):
    """Se lanza cuando falla el paso de IA. Los mensajes nunca contienen valores sensibles."""


class Risk(str, Enum):
    """Nivel de riesgo de un hallazgo."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Action(str, Enum):
    """Acción recomendada para un hallazgo."""

    ANONYMIZE = "anonymize"
    REVIEW = "review"
    KEEP = "keep"


class Classification(BaseModel):
    """Clasificación de riesgo de un hallazgo (id = índice en la lista de hallazgos)."""

    id: int
    risk: Risk
    reason: str


class Recommendation(BaseModel):
    """Acción recomendada para un hallazgo."""

    id: int
    action: Action
    reason: str


class ClassificationResult(BaseModel):
    """Salida validada de la herramienta del paso de clasificación."""

    items: list[Classification]


class RecommendationResult(BaseModel):
    """Salida validada de la herramienta del paso de recomendación."""

    items: list[Recommendation]


CLASSIFY_TOOL: dict[str, Any] = {
    "name": "report_classification",
    "description": "Informa el nivel de riesgo de cada hallazgo.",
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
    "description": "Informa la acción recomendada para cada hallazgo.",
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
    "Evalúas el riesgo de privacidad de los datos. Recibes los hallazgos de un escaneo de archivos. "
    "Los valores están enmascarados (solo se ven los 2 primeros y los 2 últimos caracteres) y nunca "
    "ves los datos reales. Juzga el riesgo según el tipo de dato y las pistas visibles. Escribe cada "
    "reason en español. Debes llamar a la herramienta report_classification exactamente una vez, con "
    "un elemento por cada id de hallazgo."
)

RECOMMEND_SYSTEM = (
    "Asesoras sobre cómo tratar los datos sensibles encontrados en un archivo. Recibes hallazgos con "
    "un tipo, un valor enmascarado y un nivel de riesgo. Para cada uno elige: anonymize (reemplazarlo), "
    "review (una persona debe revisarlo) o keep (no es lo bastante sensible como para quitarlo). "
    "Escribe cada reason en español. Debes llamar a la herramienta report_recommendations exactamente "
    "una vez, con un elemento por cada id de hallazgo."
)


def _items_payload(findings: list[Finding], extra: dict[int, dict[str, str]] | None = None) -> str:
    """Construye el JSON que se envía a la API: solo id, tipo, valor enmascarado y línea."""
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


def classifier_model() -> str:
    """Modelo para clasificar y recomendar (CLASSIFIER_MODEL en el entorno, o Haiku por defecto)."""
    return os.environ.get(ENV_CLASSIFIER_MODEL, "").strip() or DEFAULT_CLASSIFIER_MODEL


def audit_model() -> str:
    """Modelo para auditar la copia (AUDIT_MODEL en el entorno, o Sonnet por defecto)."""
    return os.environ.get(ENV_AUDIT_MODEL, "").strip() or DEFAULT_AUDIT_MODEL


def _api_reason(exc: anthropic.APIStatusError) -> str:
    """El final del mensaje: el motivo que da la API (recortado, con un solo punto final) o solo el punto."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message.strip():
        return "."
    return f": {' '.join(message.split())[:200].rstrip('.')}."


def _call_tool(
    client: anthropic.Anthropic, system: str, tool: dict[str, Any], payload: str, model: str | None = None
) -> dict[str, Any]:
    """Hace una llamada a la API y devuelve la entrada de la herramienta solicitada."""
    chosen = model or classifier_model()
    options: dict[str, Any] = {}
    if "haiku" not in chosen:  # el parámetro de esfuerzo no se envía a Haiku: no se verificó que lo admita
        options["output_config"] = {"effort": "medium"}
    try:
        response = client.messages.create(
            model=chosen,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": payload}],
            **options,
        )
    except anthropic.APIStatusError as exc:
        raise ClassifierError(f"La solicitud a la API de Claude falló (HTTP {exc.status_code}){_api_reason(exc)}") from None
    except anthropic.APIConnectionError:
        if any(ch.isspace() for ch in os.environ.get("ANTHROPIC_API_KEY", "")):
            raise ClassifierError(
                "ANTHROPIC_API_KEY contiene espacios o saltos de línea. Vuelve a copiar la clave sin ellos."
            ) from None
        raise ClassifierError("No se pudo conectar con la API de Claude. Revisa tu red.") from None
    except TypeError as exc:
        if "authentication method" not in str(exc):
            raise
        raise ClassifierError("Faltan las credenciales de la API de Claude. Define ANTHROPIC_API_KEY.") from None
    record_response_usage(tool["name"], chosen, response)  # solo cifras de tokens, nunca contenido
    if response.stop_reason == "refusal":
        raise ClassifierError("El modelo rechazó la solicitud.")
    if response.stop_reason == "max_tokens":
        raise ClassifierError("La respuesta del modelo se cortó por ser demasiado larga. Prueba con un archivo más pequeño.")
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return dict(block.input)
    raise ClassifierError(f"El modelo no llamó a la herramienta {tool['name']}.")


def _check_ids(ids: list[int], expected: int) -> None:
    """Comprueba que el modelo respondió exactamente una vez por cada hallazgo."""
    if sorted(ids) != list(range(expected)):
        raise ClassifierError("La respuesta del modelo no coincide con los hallazgos.")


def classify_findings(findings: list[Finding], client: anthropic.Anthropic | None = None) -> list[Classification]:
    """Clasifica el riesgo de cada hallazgo. A la API solo se envían valores enmascarados."""
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
            raise ClassifierError("El modelo devolvió una clasificación no válida.") from None
        _check_ids([c.id for c in result.items], len(batch))
        for c in sorted(result.items, key=lambda c: c.id):  # los ids son locales al lote: se desplazan de vuelta
            classifications.append(c.model_copy(update={"id": c.id + start}))
    return classifications


def recommend_actions(
    findings: list[Finding],
    classifications: list[Classification],
    client: anthropic.Anthropic | None = None,
) -> list[Recommendation]:
    """Recomienda una acción para cada hallazgo. A la API solo se envían valores enmascarados."""
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
            raise ClassifierError("El modelo devolvió una recomendación no válida.") from None
        _check_ids([r.id for r in result.items], len(batch))
        for r in sorted(result.items, key=lambda r: r.id):
            recommendations.append(r.model_copy(update={"id": r.id + start}))
    return recommendations

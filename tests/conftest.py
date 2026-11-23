"""Configuración de pruebas: el historial va a una carpeta temporal para no tocar data/history.db real."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # permite `from test_extractor import ...`

try:
    import anthropic  # noqa: F401
except ModuleNotFoundError:  # solo si el SDK no está instalado
    stub = types.ModuleType("anthropic")

    class _Anthropic:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("SDK de anthropic no instalado (solo relleno de pruebas).")

    stub.Anthropic = _Anthropic
    stub.APIStatusError = type("APIStatusError", (Exception,), {"status_code": 0})
    stub.APIConnectionError = type("APIConnectionError", (Exception,), {"__init__": lambda self, *a, **k: Exception.__init__(self)})
    sys.modules["anthropic"] = stub


@pytest.fixture(autouse=True)
def isolated_history(tmp_path_factory, monkeypatch):
    """Cada prueba usa su propio historial vacío."""
    monkeypatch.setenv("DATA_GUARDIAN_HISTORY", str(tmp_path_factory.mktemp("history") / "history.db"))

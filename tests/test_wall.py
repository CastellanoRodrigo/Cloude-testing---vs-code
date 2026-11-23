"""Pruebas del fondo negro de la página y del tema claro del contenido."""

import tomllib
from pathlib import Path

from data_guardian.wall import BACKGROUND, wall_css

CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"


def test_wall_css_paints_a_black_page_background() -> None:
    css = wall_css()
    assert ".stApp" in css and BACKGROUND == "#000000" and f"background-color: {BACKGROUND}" in css
    assert "data:image" not in css  # ya no hay muro de piedra
    assert "<script" not in css.lower() and "http://" not in css


def test_theme_is_light_with_dark_text() -> None:
    theme = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]
    assert theme["base"] == "light"
    assert theme["textColor"].lower() < "#5"  # un color oscuro

"""Pruebas del mensaje de bienvenida y del avatar del caballero."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from data_guardian.welcome import (
    COLORED_LEVEL,
    FRINGE_PASSES,
    GRAY_LEVEL,
    KNIGHT_VIDEO,
    SPECK_PIXELS,
    WELCOME_ES,
    knight_video_html,
)

APP = Path(__file__).resolve().parent.parent / "data_guardian" / "app.py"


def test_knight_video_html_loops_silently_and_removes_its_black_background() -> None:
    html = knight_video_html()
    for attribute in ("autoplay", "loop", "muted", "playsinline"):
        assert attribute in html
    assert "controls" not in html and "<canvas" in html  # se dibuja en un canvas, sin el fondo negro
    assert "http" not in html.lower()  # sin pedidos remotos: solo el archivo estático local
    assert "/app/static/caballero_azul.mp4" in html
    assert KNIGHT_VIDEO.is_file()


def test_knight_script_cleans_gray_specks_but_keeps_colored_sparkles() -> None:
    html = knight_video_html()
    assert "peelFringe" in html and "dropSpecks" in html
    assert f"FRINGE = {FRINGE_PASSES}" in html and f"SPECK = {SPECK_PIXELS}" in html
    assert FRINGE_PASSES >= 1 and SPECK_PIXELS > 0 and COLORED_LEVEL > GRAY_LEVEL


def test_welcome_is_only_in_spanish() -> None:
    assert "Bienvenido" in WELCOME_ES
    assert "Welcome" not in WELCOME_ES and "brave User" not in WELCOME_ES


def test_app_shows_the_spanish_welcome() -> None:
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert any(m.value == WELCOME_ES for m in at.markdown)
    assert not any("brave User" in m.value for m in at.markdown)
    assert at.title[0].value == "AI Data Guardian Lite"

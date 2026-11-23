"""Fondo negro de la página (reemplaza al antiguo muro de piedra gris)."""

BACKGROUND = "#000000"  # fondo de la página
PANEL = "#f6f7f9"  # panel claro donde va el contenido, para que se lea sobre el negro
HEADER_TEXT = "#f4f5f7"  # texto claro de la barra superior de Streamlit, que ahora queda sobre negro


def wall_css() -> str:
    """CSS (dentro de <style>) que pinta la página de negro y mantiene el contenido legible sobre un panel claro."""
    return f"""<style>
.stApp {{ background-color: {BACKGROUND}; background-image: none; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stHeader"] * {{ color: {HEADER_TEXT}; }}
[data-testid="stMainBlockContainer"] {{
  background: {PANEL}; border-radius: 16px; box-shadow: 0 2px 18px rgba(255, 255, 255, 0.08);
  margin-top: 1rem; margin-bottom: 1rem; }}
</style>"""

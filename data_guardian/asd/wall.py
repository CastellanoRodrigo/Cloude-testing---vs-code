"""Light-gray and white castle wall used as the page background (an SVG tile repeated by CSS)."""

from urllib.parse import quote

TILE_WIDTH, TILE_HEIGHT = 200, 120
STONES = ("#dde1e6", "#d3d8de", "#e5e8ec", "#d8dce2")  # light grays, white mortar between them


def _stone(x: int, y: int, index: int) -> str:
    """One rounded stone block of 96x56 (mortar is the 4 px left between blocks)."""
    return f"<rect x='{x}' y='{y}' width='96' height='56' rx='3' fill='{STONES[index % len(STONES)]}'/>"


def wall_svg() -> str:
    """One repeatable tile: two courses of stones, the second shifted by half a stone."""
    stones = [_stone(2, 2, 0), _stone(102, 2, 1), _stone(52, 62, 2), _stone(-48, 62, 3), _stone(152, 62, 1)]
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{TILE_WIDTH}' height='{TILE_HEIGHT}'>"
        f"<rect width='{TILE_WIDTH}' height='{TILE_HEIGHT}' fill='#ffffff'/>{''.join(stones)}</svg>"
    )


def wall_css() -> str:
    """CSS (inside <style>) that paints the wall behind the app and keeps the content readable."""
    tile = quote(wall_svg(), safe="/:=' ")
    return f"""<style>
.stApp {{ background-color: #ffffff; background-image: url("data:image/svg+xml,{tile}"); }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{
  background: rgba(255, 255, 255, 0.82); border-radius: 16px; box-shadow: 0 2px 18px rgba(60, 70, 80, 0.18);
  margin-top: 1rem; margin-bottom: 1rem; }}
</style>"""

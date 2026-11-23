"""Mensaje de bienvenida y avatar animado del caballero (un video en bucle servido como archivo estático)."""

from pathlib import Path

KNIGHT_VIDEO = Path(__file__).parent / "static" / "caballero_azul.mp4"
KNIGHT_VIDEO_URL = "/app/static/caballero_azul.mp4"  # lo sirve Streamlit (enableStaticServing)
KNIGHT_HEIGHT = 190  # altura del iframe que contiene al caballero (cuadrado)
KEY_LEVEL = 36  # brillo (0-255) por debajo del cual los píxeles conectados con el fondo pasan a ser transparentes
GRAY_LEVEL = 40  # un píxel cuya diferencia (máx - mín) entre canales es menor que esto cuenta como gris
COLORED_LEVEL = 60  # ... y por encima de esto como de color (las chispas azules deben sobrevivir a la limpieza)
FRINGE_PASSES = 1  # cuántos píxeles de borde gris se pelan del contorno del caballero
SPECK_PIXELS = 80  # los grupos grises menores que esto, lejos del caballero, se descartan
SIZE = 256  # el video se dibuja en un lienzo de SIZE x SIZE

KNIGHT_SCRIPT = """
const v = document.getElementById('v'), c = document.getElementById('c');
const x = c.getContext('2d', { willReadFrequently: true });
const n = %(size)d, mask = new Uint8Array(n * n), seen = new Uint8Array(n * n), queue = new Int32Array(n * n);
const FRINGE = %(fringe)d, SPECK = %(speck)d, GRAY = %(gray)d, COLORED = %(colored)d;
function neighbors(p) {
  const out = [], col = p %% n;
  if (p >= n) out.push(p - n);
  if (p < n * (n - 1)) out.push(p + n);
  if (col > 0) out.push(p - 1);
  if (col < n - 1) out.push(p + 1);
  return out;
}
function keyBackground(d) {
  mask.fill(0); let head = 0, tail = 0;
  const dark = (p) => Math.max(d[4 * p], d[4 * p + 1], d[4 * p + 2]) < %(level)d;
  const push = (p) => { if (!mask[p] && dark(p)) { mask[p] = 1; queue[tail++] = p; } };
  for (let i = 0; i < n; i++) { push(i); push((n - 1) * n + i); push(i * n); push(i * n + n - 1); }
  while (head < tail) for (const q of neighbors(queue[head++])) push(q);
}
function spread(d, p) {
  const r = d[4 * p], g = d[4 * p + 1], b = d[4 * p + 2];
  return Math.max(r, g, b) - Math.min(r, g, b);
}
function peelFringe(d) {
  for (let pass = 0; pass < FRINGE; pass++) {
    const peel = [];
    for (let p = 0; p < n * n; p++)
      if (!mask[p] && spread(d, p) < GRAY && neighbors(p).some((q) => mask[q])) peel.push(p);
    for (const p of peel) mask[p] = 1;
  }
}
function dropSpecks(d) {
  seen.fill(0);
  for (let start = 0; start < n * n; start++) {
    if (mask[start] || seen[start]) continue;
    let head = 0, tail = 0, colored = 0;
    seen[start] = 1; queue[tail++] = start;
    while (head < tail) {
      const p = queue[head++];
      if (spread(d, p) >= COLORED) colored++;
      for (const q of neighbors(p)) if (!mask[q] && !seen[q]) { seen[q] = 1; queue[tail++] = q; }
    }
    if (tail < SPECK && colored * 2 < tail) for (let i = 0; i < tail; i++) mask[queue[i]] = 1;
  }
}
function key(d) {
  keyBackground(d); peelFringe(d); dropSpecks(d);
  for (let p = 0; p < n * n; p++) if (mask[p]) d[4 * p + 3] = 0;
}
function frame() {
  if (v.readyState >= 2) {
    x.drawImage(v, 0, 0, n, n);
    try { const img = x.getImageData(0, 0, n, n); key(img.data); x.putImageData(img, 0, 0); }
    catch (e) { c.style.display = 'none'; v.style.display = 'block'; return; }
  }
  requestAnimationFrame(frame);
}
v.addEventListener('loadeddata', () => { v.play(); frame(); });
""" % {
    "size": SIZE, "level": KEY_LEVEL, "fringe": FRINGE_PASSES, "speck": SPECK_PIXELS,
    "gray": GRAY_LEVEL, "colored": COLORED_LEVEL,
}


def knight_video_html() -> str:
    """Devuelve el caballero como una página HTML pequeña: un video silencioso en bucle dibujado en un lienzo, con el negro eliminado.

    Se muestra dentro de un iframe: el sanitizador de markdown de Streamlit elimina `muted` (los navegadores
    bloquean la reproducción automática de videos sin silenciar) y los scripts. Si no se puede leer el lienzo,
    se muestra el video sin procesar.
    """
    return (
        '<body style="margin:0;background:transparent">'
        f'<video id="v" src="{KNIGHT_VIDEO_URL}" autoplay loop muted playsinline style="display:none;width:100%"></video>'
        f'<canvas id="c" width="{SIZE}" height="{SIZE}" role="img" aria-label="Avatar del caballero" '
        'style="width:100%;height:auto;display:block"></canvas>'
        f"<script>{KNIGHT_SCRIPT}</script></body>"
    )


WELCOME_ES = (
    "**¡Bienvenido, valiente usuario!** Soy Sir Guardian. Dame un archivo TXT o CSV y cazaré "
    "direcciones de correo, números de identificación y claves de API; luego te entregaré una copia limpia y anonimizada."
)

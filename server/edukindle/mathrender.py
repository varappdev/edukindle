"""Display maths -> PNG, using matplotlib's mathtext (no TeX install needed).

Images are black ink on a transparent background (grey + alpha PNG), rendered
at the Kindle's own text size so they need no rescaling on the device. The
Kindle composites them with cairo, which reads PNG natively.

mathtext covers fractions, roots, sums, integrals, limits, Greek, accents and
\\left/\\right. Anything it can't parse (matrices, cases) returns None and
the caller falls back to an inline-markup rendering of the formula.
"""

from __future__ import annotations

import base64
import functools
import io
import logging
import re
import threading

log = logging.getLogger("edukindle.math")

# DejaVu Serif's sturdy strokes survive E-ink's low contrast better than STIX.
FONTSET = "dejavuserif"
# DejaVu's x-height is smaller than Bookerly's; scale up so formulas match the text.
SIZE_FACTOR = 1.12
MIN_PX, MAX_PX = 16, 160

_lock = threading.Lock()  # matplotlib's global state isn't thread-safe
_mpl = None


def _matplotlib():
    global _mpl
    if _mpl is None:
        import matplotlib

        matplotlib.use("Agg")
        matplotlib.rcParams["mathtext.fontset"] = FONTSET
        from matplotlib.font_manager import FontProperties
        from matplotlib.mathtext import MathTextParser

        _mpl = (MathTextParser("agg"), FontProperties)
    return _mpl


def _prepare(tex: str, display: bool) -> str:
    tex = tex.strip()
    tex = re.sub(r"\\(displaystyle|textstyle|limits|nolimits)\b", "", tex)
    if display:  # full-size fractions, like LaTeX's display style
        tex = re.sub(r"\\frac(?![a-zA-Z])", r"\\dfrac", tex)
    else:  # in running text, limits go beside the operator, not above and below
        tex = re.sub(r"\\(sum|prod|coprod|lim|bigcup|bigcap|bigoplus)(?![a-zA-Z])(?=\s*[_^])",
                     r"{\\\1}", tex)
    tex = re.sub(r"\\(left|right)\s*\\?\.", "", tex)  # invisible delimiters
    tex = tex.replace("\\operatorname*", "\\operatorname")
    return tex


@functools.lru_cache(maxsize=2048)
def render(tex: str, px: int, display: bool = True) -> tuple[str, int, int, int] | None:
    """Returns (base64 PNG, width, height, depth) or None if mathtext can't
    parse it. depth is how far the image extends below the text baseline,
    which is what lets inline maths sit on the line."""
    px = max(MIN_PX, min(MAX_PX, round(int(px) * SIZE_FACTOR)))
    source = _prepare(tex, display)
    if not source or "\\begin" in source:
        return None
    try:
        with _lock:
            parser, FontProperties = _matplotlib()
            # At 72 dpi a font size of N points is N pixels.
            out = parser.parse(f"${source}$", dpi=72, prop=FontProperties(size=px))
    except Exception as e:  # mathtext raises ValueError and friends on bad input
        log.info("can't render %r as an image (%s); using text", tex, str(e).splitlines()[0])
        return None

    import numpy as np
    from PIL import Image

    ink = np.asarray(out.image, dtype=np.uint8)  # 255 = ink
    if not ink.any():
        return None
    depth = int(round(out.depth))
    if display:  # a little air around display formulas; inline ones sit on the line
        pad = max(1, px // 16)
        ink = np.pad(ink, pad)
        depth += pad
    height, width = ink.shape
    image = Image.new("LA", (width, height), 0)
    image.putalpha(Image.fromarray(ink, mode="L"))
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), width, height, depth


def warm_up() -> None:
    """The first render loads fonts (~0.5 s); do it before a student is waiting."""
    try:
        render(r"\frac{a}{b}", 40)
    except Exception:  # never let warm-up take the server down
        log.exception("maths renderer warm-up failed")

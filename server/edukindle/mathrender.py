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
        from matplotlib import mathtext
        from matplotlib.font_manager import FontProperties

        _mpl = (mathtext, FontProperties)
    return _mpl


def _prepare(tex: str) -> str:
    tex = tex.strip()
    tex = re.sub(r"\\(displaystyle|textstyle|limits|nolimits)\b", "", tex)
    # Display maths: full-size fractions, like LaTeX's display style.
    tex = re.sub(r"\\frac(?![a-zA-Z])", r"\\dfrac", tex)
    tex = re.sub(r"\\(left|right)\s*\\?\.", "", tex)  # invisible delimiters
    tex = tex.replace("\\operatorname*", "\\operatorname")
    return tex


@functools.lru_cache(maxsize=1024)
def render(tex: str, px: int) -> tuple[str, int, int] | None:
    """Returns (base64 PNG, width, height) or None if mathtext can't parse it."""
    px = max(MIN_PX, min(MAX_PX, round(int(px) * SIZE_FACTOR)))
    source = _prepare(tex)
    if not source or "\\begin" in source:
        return None
    try:
        with _lock:
            mathtext, FontProperties = _matplotlib()
            buf = io.BytesIO()
            # At 72 dpi a font size of N points is N pixels.
            mathtext.math_to_image(f"${source}$", buf, prop=FontProperties(size=px), dpi=72,
                                   format="png")
    except Exception as e:  # mathtext raises ValueError and friends on bad input
        log.info("can't render %r as an image (%s); using text", tex, str(e).splitlines()[0])
        return None

    from PIL import Image

    grey = Image.open(io.BytesIO(buf.getvalue())).convert("L")
    bbox = grey.point(lambda v: 255 - v).getbbox()  # trim the white margin
    if not bbox:
        return None
    pad = max(1, px // 12)
    grey = grey.crop((max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
                      min(bbox[2] + pad, grey.width), min(bbox[3] + pad, grey.height)))
    ink = Image.new("LA", grey.size, 0)
    ink.putalpha(grey.point(lambda v: 255 - v))
    out = io.BytesIO()
    ink.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii"), ink.width, ink.height


def warm_up() -> None:
    """The first render loads fonts (~0.5 s); do it before a student is waiting."""
    try:
        render(r"\frac{a}{b}", 40)
    except Exception:  # never let warm-up take the server down
        log.exception("maths renderer warm-up failed")

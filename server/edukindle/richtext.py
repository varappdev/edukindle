"""Markdown + LaTeX -> blocks of Pango markup for the Kindle's renderer.

The Kindle draws text with Pango 1.26, so the server does all the parsing and
sends ready-to-draw blocks:

- inline formatting and inline maths become Pango markup (maths as Unicode
  with <sup>/<sub>, which the Kindle's Bookerly font covers);
- display maths becomes a PNG rendered at the Kindle's text size
  (see mathrender), with a markup fallback;
- code keeps its whitespace for the Kindle's monospace font.

Supported Markdown subset: paragraphs, # ## ### headings, - * + and 1. lists
(nested by indentation), > notes, ``` fenced code, $$ display maths $$ (or
\\[ \\]), --- rules, simple | tables |, **bold**, *italic*, `code`,
~~strike~~, [links](url), $inline maths$ (or \\( \\)).

A "paragraph" is a top-level chunk separated by blank lines. Blocks carry the
index of the paragraph they came from, which is how a tap on the Kindle maps
back to the text the student wants explained.
"""

from __future__ import annotations

import html
import re
import textwrap
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any

from . import mathrender

# The Kindle's monospace font first (its four files all claim the same weight, so
# name the Regular file's unique family); the rest are for the desktop build.
MONO = "KindleBlackboxC Regular, DejaVu Sans Mono, Liberation Mono, monospace"

# Bullet glyphs PDFs use; '◦' and '·' are left out because maths uses them as operators.
BULLET_GLYPHS = "\u2022\u25cf\u25aa\u25a0\u2023\u25ba\u27a2\u2756"  # • ● ▪ ■ ‣ ► ➢ ❖

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$")
# "- x", "1. x" need a space after the marker; bullet glyphs often come glued on ("•Text").
_ITEM = re.compile(rf"^(\s*)(?:([-*+]|\d{{1,3}}[.)])\s+|([{BULLET_GLYPHS}])\s*)(.*)$")
_GLUED_BULLET = re.compile(rf"(?:^|(?<=\s))[{BULLET_GLYPHS}]\s*")
# PDF debris: variation selectors, pieces of tall brackets, object-replacement chars.
_JUNK = re.compile("[\ufe00-\ufe0f\u239b-\u23ad\ufffc]")
# Mathematical alphanumerics (𝑛, 𝐴, 𝛿, ℎ): no Kindle font has them.
_MATH_ALNUM = "\U0001D400-\U0001D7FF\u210e"
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

BULLETS = ["\u2022", "\u25e6", "\u25aa"]  # • ◦ ▪ by nesting level


# --- paragraphs ---------------------------------------------------------------


def _is_math_open(s: str) -> bool:
    return s.startswith("$$") or s.startswith("\\[")


def paragraphs(md: str) -> list[str]:
    """Top-level chunks separated by blank lines. Fenced code and display
    maths stay whole even if they contain blank lines."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chunks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            chunks.append("\n".join(buf))
            buf.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        fence = _FENCE.match(line)
        if fence:
            flush()
            mark = fence.group(1)
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(mark):
                j += 1
            chunks.append("\n".join(lines[i:j + 1]))
            i = j + 1
            continue
        if _is_math_open(s) and not buf:
            close = "$$" if s.startswith("$$") else "\\]"
            if close in s[2:]:
                chunks.append(s)
                i += 1
                continue
            j = i + 1
            while j < len(lines) and close not in lines[j]:
                j += 1
            chunks.append("\n".join(l.strip() for l in lines[i:j + 1]))
            i = j + 1
            continue
        if s:
            buf.append(line.rstrip())
        else:
            flush()
        i += 1
    flush()
    return [c for c in chunks if c.strip()]


def kind_of(chunk: str) -> str:
    """'code', 'math', 'structured' (lists, quotes, tables, headings) or 'prose'."""
    first = chunk.split("\n", 1)[0]
    if _FENCE.match(first):
        return "code"
    if _is_math_open(first.strip()):
        return "math"
    for line in chunk.split("\n"):
        if (_ITEM.match(line) or _QUOTE.match(line) or _TABLE.match(line)
                or _HEADING.match(line) or _RULE.match(line)):
            return "structured"
    return "prose"


def normalise(chunk: str) -> str:
    """Tidy whitespace without destroying structure: prose collapses to one
    line, lists keep one line per item, code and maths stay verbatim."""
    kind = kind_of(chunk)
    if kind in ("code", "math"):
        return "\n".join(l.rstrip() for l in chunk.strip("\n").split("\n"))
    if kind == "structured":
        out = []
        for line in chunk.split("\n"):
            indent = len(line) - len(line.lstrip(" "))
            text = re.sub(r"\s+", " ", line).strip()
            if text:
                out.append(" " * indent + text)
        return "\n".join(out)
    return re.sub(r"\s+", " ", chunk).strip()


def join(chunks: list[str]) -> str:
    return "\n\n".join(c for c in chunks if c.strip())


# --- inline Markdown -> Pango markup --------------------------------------------

_INLINE = re.compile(
    rf"(?P<alnum>[{_MATH_ALNUM}]+)"
    r"|(?P<tick>`+)(?P<code>.+?)(?P=tick)"
    r"|\\\((?P<m1>.+?)\\\)"
    r"|\$\$(?P<m2>.+?)\$\$"
    r"|(?<![\\$\w])\$(?![\s$])(?P<m3>(?:\\.|[^$\\\n])+?)(?<![\s\\])\$(?![\d$])"
    r"|\\(?P<esc>[\\`*_$\[\]#+\-.!{}()~>|])",
    re.S,
)
_PLACEHOLDER = re.compile("[\ue000-\uf8ff]")


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def _emphasis(s: str) -> str:
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"<i>\1</i>", s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", s)
    s = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", r"<u>\1</u>", s)
    return s


def well_formed(markup: str) -> bool:
    try:
        ET.fromstring(f"<r>{markup}</r>")
        return True
    except ET.ParseError:
        return False


# Inline maths that reads badly as a line of text gets typeset as an image.
_COMPLEX_TEX = re.compile(
    r"\\(?:[dtc]?frac|sum|prod|i*int|oint|lim|binom|begin|overline|underbrace|overbrace"
    r"|overset|underset|stackrel)(?![A-Za-z])|\\sqrt\s*(?:\[[^\]]*\])?\s*\{[^{}]{2,}")
OBJECT = "\ufffc"  # stands in for an inline image in the markup


def _math_alnum(run: str) -> str:
    """𝑛 -> <i>n</i>, 𝐀 -> <b>A</b>, 𝔸 -> 𝔸's ℝ-style letter..."""
    out = []
    for ch in run:
        base = unicodedata.normalize("NFKC", ch)
        name = unicodedata.name(ch, "")
        if "DOUBLE-STRUCK" in name:
            base = BLACKBOARD.get(base, base)
        e = esc(base)
        if "BOLD" in name and "ITALIC" in name:
            e = f"<b><i>{e}</i></b>"
        elif "ITALIC" in name or ch == "\u210e":  # ℎ is the italic h
            e = f"<i>{e}</i>"
        elif "BOLD" in name:
            e = f"<b>{e}</b>"
        elif "MONOSPACE" in name:
            e = f'<span font_family="{MONO}">{e}</span>'
        out.append(e)
    return "".join(out).replace("</i><i>", "").replace("</i></b><b><i>", "")


def inline(text: str, images: list | None = None, px: int | None = None) -> str:
    """Inline Markdown and maths -> Pango markup. Always well-formed.

    With `images` (a list to fill) and the reader's text size `px`, complex
    inline maths becomes an OBJECT character plus an entry in `images`
    {png, w, h, d (depth below the baseline)}, which the Kindle draws in line.
    """
    if not text:
        return ""
    protected: list[str] = []

    def keep(markup: str) -> str:
        protected.append(markup)
        return chr(0xE000 + len(protected) - 1)

    def repl(m: re.Match) -> str:
        if m.group("alnum") is not None:
            return keep(_math_alnum(m.group("alnum")))
        if m.group("code") is not None:
            return keep(f'<span font_family="{MONO}">{esc(m.group("code").strip())}</span>')
        if m.group("esc") is not None:
            return keep(esc(m.group("esc")))
        tex = m.group("m1") or m.group("m2") or m.group("m3") or ""
        if images is not None and px and _COMPLEX_TEX.search(tex):
            img = mathrender.render(tex, px, display=False)
            if img:
                images.append({"png": img[0], "w": img[1], "h": img[2], "d": img[3]})
                return keep(OBJECT)
        return keep(tex_to_markup(tex))

    body = _INLINE.sub(repl, _JUNK.sub("", _PLACEHOLDER.sub("", text)))
    markup = _emphasis(esc(body))
    markup = _PLACEHOLDER.sub(lambda m: protected[ord(m.group()) - 0xE000], markup)
    if well_formed(markup):
        return markup
    # Mismatched emphasis markers: keep the maths and code, drop the emphasis.
    plain = _PLACEHOLDER.sub(lambda m: protected[ord(m.group()) - 0xE000], esc(body))
    return plain if well_formed(plain) else esc(text)


# --- LaTeX maths -> Pango markup ---------------------------------------------------

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# kind: o = operand, b = binary operator, r = relation, p = punctuation
SYMBOLS: dict[str, tuple[str, str]] = {
    "cdot": ("·", "b"), "times": ("×", "b"), "div": ("÷", "b"), "pm": ("±", "b"),
    "mp": ("∓", "b"), "ast": ("∗", "b"), "star": ("⋆", "b"), "circ": ("∘", "b"),
    "bullet": ("•", "b"), "cup": ("∪", "b"), "cap": ("∩", "b"), "wedge": ("∧", "b"),
    "land": ("∧", "b"), "vee": ("∨", "b"), "lor": ("∨", "b"), "oplus": ("⊕", "b"),
    "otimes": ("⊗", "b"), "setminus": ("∖", "b"),
    "le": ("≤", "r"), "leq": ("≤", "r"), "ge": ("≥", "r"), "geq": ("≥", "r"),
    "ne": ("≠", "r"), "neq": ("≠", "r"), "approx": ("≈", "r"), "equiv": ("≡", "r"),
    "sim": ("∼", "r"), "simeq": ("≃", "r"), "cong": ("≅", "r"), "propto": ("∝", "r"),
    "ll": ("≪", "r"), "gg": ("≫", "r"), "in": ("∈", "r"), "notin": ("∉", "r"),
    "ni": ("∋", "r"), "subset": ("⊂", "r"), "subseteq": ("⊆", "r"),
    "supset": ("⊃", "r"), "supseteq": ("⊇", "r"), "to": ("→", "r"),
    "rightarrow": ("→", "r"), "leftarrow": ("←", "r"), "gets": ("←", "r"),
    "leftrightarrow": ("↔", "r"), "Rightarrow": ("⇒", "r"), "implies": ("⇒", "r"),
    "Leftarrow": ("⇐", "r"), "Leftrightarrow": ("⇔", "r"), "iff": ("⇔", "r"),
    "mapsto": ("↦", "r"), "longrightarrow": ("→", "r"), "Longrightarrow": ("⇒", "r"),
    "mid": ("|", "r"), "parallel": ("∥", "r"), "perp": ("⊥", "r"),
    "infty": ("∞", "o"), "partial": ("∂", "o"), "nabla": ("∇", "o"),
    "forall": ("∀", "o"), "exists": ("∃", "o"), "emptyset": ("∅", "o"),
    "varnothing": ("∅", "o"), "ell": ("ℓ", "o"), "hbar": ("ℏ", "o"),
    "angle": ("∠", "o"), "triangle": ("△", "o"), "neg": ("¬", "o"), "lnot": ("¬", "o"),
    "ldots": ("…", "o"), "dots": ("…", "o"), "cdots": ("⋯", "o"), "vdots": ("⋮", "o"),
    "prime": ("′", "o"), "degree": ("°", "o"), "therefore": ("∴", "r"),
    "because": ("∵", "r"), "langle": ("⟨", "o"), "rangle": ("⟩", "o"),
    "lfloor": ("⌊", "o"), "rfloor": ("⌋", "o"), "lceil": ("⌈", "o"), "rceil": ("⌉", "o"),
    "lvert": ("|", "o"), "rvert": ("|", "o"), "vert": ("|", "o"), "|": ("‖", "o"),
    "lVert": ("‖", "o"), "rVert": ("‖", "o"), "Vert": ("‖", "o"),
    "{": ("{", "o"), "}": ("}", "o"), "%": ("%", "o"), "$": ("$", "o"),
    "#": ("#", "o"), "&": ("&amp;", "o"), "_": ("_", "o"),
}
BIG = {"sum": "∑", "prod": "∏", "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
       "bigcup": "⋃", "bigcap": "⋂"}
FUNCS = {"sin", "cos", "tan", "sec", "csc", "cot", "arcsin", "arccos", "arctan", "sinh",
         "cosh", "tanh", "log", "ln", "lg", "exp", "lim", "limsup", "liminf", "max",
         "min", "sup", "inf", "det", "gcd", "lcm", "deg", "arg", "dim", "ker", "Pr",
         "mod", "bmod"}
SPACES = {",": "\u2009", ":": " ", ";": " ", " ": " ", "quad": "\u2003",
          "qquad": "\u2003\u2003", "!": "", "enspace": " ", "thinspace": "\u2009"}
SIZING = {"left", "right", "middle", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl",
          "Bigr", "biggl", "biggr", "displaystyle", "textstyle", "scriptstyle", "limits",
          "nolimits"}
ACCENTS = {"hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0304",
           "dot": "\u0307", "ddot": "\u0308", "tilde": "\u0303", "widetilde": "\u0303"}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ"}
_SUP_CHARS = str.maketrans("0123456789+-−=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ")
_SUB_CHARS = str.maketrans("0123456789+-−=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎")

_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|[{}^_&\[\]]|\s+|.", re.S)


class _Atom:
    __slots__ = ("m", "kind")

    def __init__(self, m: str, kind: str = "o"):
        self.m = m
        # o operand, b binary, r relation, p punctuation, f function name,
        # F linear fraction, s space, ( and ) brackets
        self.kind = kind


class _TexParser:
    def __init__(self, tex: str):
        self.toks = _TOKEN.findall(tex)
        self.i = 0

    def _peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _skip_space(self) -> None:
        while self._peek() is not None and self._peek().isspace():
            self.i += 1

    def _raw_group(self) -> str:
        """Text of the next {group}, spaces kept (for \\text)."""
        self._skip_space()
        if self._peek() != "{":
            tok = self._peek()
            self.i += 1
            return tok or ""
        self.i += 1
        depth, out = 1, []
        while self._peek() is not None:
            t = self._peek()
            self.i += 1
            if t == "{":
                depth += 1
            elif t == "}":
                depth -= 1
                if depth == 0:
                    break
            out.append(t[1:] if t.startswith("\\") and len(t) == 2 else t)
        return "".join(out)

    def _arg(self) -> str:
        """Rendered markup of the next argument: a {group} or a single token."""
        self._skip_space()
        t = self._peek()
        if t is None:
            return ""
        if t == "{":
            self.i += 1
            return _render(self.parse("}"))
        self.i += 1
        if t.startswith("\\"):
            atoms: list[_Atom] = []
            self._command(t[1:], atoms)
            return _render(atoms)
        return _render([self._char(t)])

    def _optional(self) -> str | None:
        self._skip_space()
        if self._peek() != "[":
            return None
        self.i += 1
        return _render(self.parse("]"))

    @staticmethod
    def _char(t: str) -> _Atom:
        if t.isalpha() and t.isascii():
            return _Atom(f"<i>{t}</i>")
        if t.isdigit() or t == ".":
            return _Atom(t)
        if t == "-":
            return _Atom("−", "b")
        if t in "+*":
            return _Atom("+" if t == "+" else "∗", "b")
        if t in "=<>":
            return _Atom(esc(t), "r")
        if t in "([":
            return _Atom(t, "(")
        if t in ")]":
            return _Atom(t, ")")
        if t in ",;":
            return _Atom(t, "p")
        if t == "!":
            return _Atom("!", ")")
        return _Atom(esc(t))

    def parse(self, stop: str | None = None) -> list[_Atom]:
        atoms: list[_Atom] = []
        while self._peek() is not None:
            t = self._peek()
            self.i += 1
            if t == stop:
                return atoms
            if t.isspace() or t == "}":
                continue
            if t in "^_":
                self._script(atoms, "sup" if t == "^" else "sub")
            elif t == "{":
                atoms.append(_Atom(_render(self.parse("}"))))
            elif t == "&":
                atoms.append(_Atom("\u2003", "s"))
            elif t == "'":
                if atoms:
                    atoms[-1].m = (atoms[-1].m + "′").replace("′′′", "‴").replace("″′", "‴").replace("′′", "″")
                else:
                    atoms.append(_Atom("′"))
            elif t.startswith("\\"):
                self._command(t[1:], atoms)
            else:
                atoms.append(self._char(t))
        return atoms

    def _script(self, atoms: list[_Atom], tag: str) -> None:
        arg = self._arg()
        if tag == "sup" and arg in ("′", "″", "‴", "°", "∘"):
            content = "°" if arg == "∘" else arg  # these glyphs are already raised
        else:
            # Scripts are small: no spaces around their operators (k=1, n−1).
            arg = re.sub(r" ?([=+−→←<>≤≥≠∈×·]|&lt;|&gt;) ?", r"\1", arg)
            content = f"<{tag}>{arg}</{tag}>"
        if not atoms:
            atoms.append(_Atom(""))
        atoms[-1].m += content
        if atoms[-1].kind in ("b", "r", "("):
            atoms[-1].kind = "o"

    def _command(self, name: str, atoms: list[_Atom]) -> None:
        if name in GREEK:
            g = GREEK[name]
            atoms.append(_Atom(f"<i>{g}</i>" if name[0].islower() else g))
        elif name in SYMBOLS:
            m, kind = SYMBOLS[name]
            atoms.append(_Atom(m, kind))
        elif name in SPACES:
            atoms.append(_Atom(SPACES[name], "s"))
        elif name in SIZING:
            if name in ("left", "right", "middle"):
                self._skip_space()
                t = self._peek()
                if t == ".":
                    self.i += 1
        elif name in BIG:
            atoms.append(_Atom(BIG[name], "f"))
        elif name in FUNCS:
            atoms.append(_Atom("mod" if name == "bmod" else name, "f"))
        elif name in ("frac", "dfrac", "tfrac", "cfrac"):
            num, den = self._arg(), self._arg()
            frac = _fraction(num, den)
            atoms.append(_Atom(frac, "o" if "\u2044" in frac else "F"))
        elif name == "binom":
            n, k = self._arg(), self._arg()
            atoms.append(_Atom(f"C({n}, {k})"))
        elif name == "sqrt":
            index = self._optional()
            body = self._arg()
            root = (f"<sup>{index}</sup>" if index else "") + "√"
            atoms.append(_Atom(root + (body if _simple(body) else f"({body})")))
        elif name in ("text", "textrm", "textup", "mathrm", "operatorname", "textnormal",
                      "mbox", "textsf", "mathsf"):
            atoms.append(_Atom(esc(self._raw_group()), "f" if name == "operatorname" else "o"))
        elif name in ("textit", "mathit", "emph"):
            atoms.append(_Atom(f"<i>{esc(self._raw_group())}</i>"))
        elif name in ("textbf", "mathbf", "boldsymbol", "bm", "vec", "overrightarrow"):
            atoms.append(_Atom(f"<b>{_strip_italic(self._arg())}</b>"))
        elif name in ("mathtt", "texttt"):
            atoms.append(_Atom(f'<span font_family="{MONO}">{esc(self._raw_group())}</span>'))
        elif name in ("mathbb", "mathbbm"):
            raw = self._raw_group()
            atoms.append(_Atom("".join(BLACKBOARD.get(c, c) for c in raw)))
        elif name in ("mathcal", "mathscr", "mathfrak"):
            atoms.append(_Atom(self._arg()))
        elif name in ACCENTS:
            body = self._arg()
            visible = re.sub(r"<[^>]+>", "", body)
            if len(visible) != 1:
                atoms.append(_Atom(body))
            elif body.endswith("</i>"):  # keep the mark in the letter's italic run
                atoms.append(_Atom(body[:-4] + ACCENTS[name] + "</i>"))
            else:
                atoms.append(_Atom(body + ACCENTS[name]))
        elif name in ("underline", "underbrace", "overbrace", "boxed", "phantom", "hphantom"):
            body = self._arg()
            atoms.append(_Atom("" if "phantom" in name else body))
        elif name == "begin":
            self._environment(self._raw_group(), atoms)
        elif name == "end":
            self._raw_group()
        elif name == "\\":
            atoms.append(_Atom(";", "p"))
        elif len(name) == 1 and not name.isalpha():
            atoms.append(_Atom(esc(name)))
        else:
            atoms.append(_Atom(esc(name), "f"))  # unknown command: show its name

    def _environment(self, env: str, atoms: list[_Atom]) -> None:
        """Matrices, cases and aligned rows flattened onto one line."""
        depth, start = 1, self.i
        while self.i < len(self.toks):
            t = self.toks[self.i]
            if t == "\\begin":
                depth += 1
            elif t == "\\end":
                depth -= 1
                if depth == 0:
                    break
            self.i += 1
        inner = "".join(self.toks[start:self.i])
        if self.i < len(self.toks):
            self.i += 1
            self._raw_group()
        rows = [r for r in re.split(r"\\\\", inner) if r.strip()]
        cells = [[tex_to_markup(c) for c in r.split("&")] for r in rows]
        base = env.rstrip("*")
        if base == "cases":
            body = "; ".join(", ".join(c for c in row if c) for row in cells)
            atoms.append(_Atom("{ " + body + " }"))
        elif base.endswith("matrix"):
            body = "; ".join("\u2003".join(row) for row in cells)
            left, right = {"pmatrix": "()", "bmatrix": "[]", "vmatrix": "||",
                           "Bmatrix": "{}"}.get(base, "[]")
            atoms.append(_Atom(f"{left}{body}{right}"))
        else:
            atoms.append(_Atom("; ".join(" ".join(row) for row in cells)))


def _strip_italic(m: str) -> str:
    return m.replace("<i>", "").replace("</i>", "")


def _visible(m: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", m))


def _simple(m: str) -> bool:
    """Short and operator-free, so it needs no brackets."""
    v = _visible(m)
    return len(v) <= 4 and not re.search(r"[\s+−=<>≤≥,/·×]", v)


def _fraction(num: str, den: str) -> str:
    vn, vd = _visible(num), _visible(den)
    if re.fullmatch(r"\d{1,3}", vn) and re.fullmatch(r"\d{1,3}", vd):
        return f"<sup>{num}</sup>\u2044<sub>{den}</sub>"  # ½-style vulgar fraction
    left = num if _simple(num) else f"({num})"
    right = den if _simple(den) else f"({den})"
    return f"{left}/{right}"


def _render(atoms: list[_Atom]) -> str:
    out: list[str] = []
    prev = None  # kind of the previous non-space atom
    for a in atoms:
        if a.kind == "b":
            out.append(f" {a.m} " if prev in ("o", ")", "f", "F") else a.m)
        elif a.kind == "r":
            out.append(f" {a.m} ")
        elif a.kind == "p":
            out.append(f"{a.m} ")
        else:
            # Thin spaces where juxtaposition would be ambiguous: "sin x",
            # "d/dx x²", "2 x/3".
            if (prev == "f" and a.kind in ("o", "f", "F")) or (
                    prev == "F" and a.kind in ("o", "f", "F", "(")) or (
                    prev in ("o", ")") and a.kind == "F"):
                out.append("\u2009")
            out.append(a.m)
        if a.kind != "s":
            prev = a.kind
    s = "".join(out)
    s = re.sub(r" {2,}", " ", s).strip(" ")
    return s.replace("</i><i>", "")


def tex_to_markup(tex: str) -> str:
    """LaTeX maths -> one line of Pango markup (Unicode, <sup>, <sub>, italics)."""
    try:
        markup = _render(_TexParser(tex.strip()).parse())
    except Exception:  # odd model output must never break a page
        markup = esc(tex)
    return markup if well_formed(markup) else esc(tex)


def markup_to_plain(markup: str) -> str:
    """Pango markup -> plain Unicode text (super/subscripts where possible)."""
    def script(m: re.Match, table, mark: str) -> str:
        inner = _visible(m.group(2))
        conv = inner.translate(table)
        if all(ord(c) > 127 or c in " " for c in conv) and conv:
            return conv
        return f"{mark}{inner}" if len(inner) == 1 else f"{mark}({inner})"
    s = re.sub(r"<(sup)>(.*?)</sup>", lambda m: script(m, _SUP_CHARS, "^"), markup)
    s = re.sub(r"<(sub)>(.*?)</sub>", lambda m: script(m, _SUB_CHARS, "_"), s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


# --- blocks -------------------------------------------------------------------------------


def _strip_math_delims(chunk: str) -> str:
    s = chunk.strip()
    if s.startswith("$$"):
        s = s[2:]
        s = s[:-2] if s.endswith("$$") else s
    elif s.startswith("\\["):
        s = s[2:]
        s = s[:-2] if s.endswith("\\]") else s
    return s.strip()


_ALIGN_ENVS = re.compile(r"\\(begin|end)\{(aligned|align\*?|gather\*?|gathered|equation\*?"
                         r"|eqnarray\*?|split|multline\*?)\}")


def math_rows(tex: str) -> list[str]:
    """Split aligned/gathered display maths into one formula per row."""
    if not _ALIGN_ENVS.search(tex):
        return [tex]
    body = _ALIGN_ENVS.sub("", tex)
    rows = [r.replace("&", "").strip() for r in re.split(r"\\\\(?:\[[^\]]*\])?", body)]
    return [r for r in rows if r] or [tex]


def _split_relations(tex: str) -> list[str]:
    """Split before each top-level '=' / '\\approx' / '\\le' etc.:
    'a = b = c' -> ['a', '= b', '= c']."""
    parts, depth, start, i = [], 0, 0, 0
    rel = re.compile(r"=|\\(?:approx|le|leq|ge|geq|ne|neq|equiv|Rightarrow|implies)(?![A-Za-z])")
    while i < len(tex):
        c = tex[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif tex.startswith("\\left", i):
            depth += 1
        elif tex.startswith("\\right", i):
            depth -= 1
        elif depth == 0 and (m := rel.match(tex, i)) and i > start:
            parts.append(tex[start:i].strip())
            start = i
            i = m.end()
            continue
        i += 1
    parts.append(tex[start:].strip())
    return [p for p in parts if p]


def _math_rows_fitting(tex: str, px: int, width: int) -> list[str]:
    """Greedily pack 'lhs', '= a', '= b' pieces into rows no wider than width."""
    pieces = _split_relations(tex)
    if len(pieces) < 3:
        return [tex]
    rows = [pieces[0] + " " + pieces[1]]
    for piece in pieces[2:]:
        joined = rows[-1] + " " + piece
        img = mathrender.render(joined, px)
        if img and img[1] <= width:
            rows[-1] = joined
        else:
            rows.append(piece)
    return rows


def _math_blocks(tex: str, para: int, px: int | None,
                 width: int | None) -> list[dict[str, Any]]:
    rows = [tex]
    if px and width:
        img = mathrender.render(tex, px)
        if img and img[1] > width:
            rows = _math_rows_fitting(tex, px, width)
    blocks = []
    for row in rows:
        block: dict[str, Any] = {"t": "math", "m": tex_to_markup(row), "para": para}
        if px:
            img = mathrender.render(row, px)
            if img:
                block.update(png=img[0], w=img[1], h=img[2])
        blocks.append(block)
    return blocks


def _table_text(lines: list[str]) -> str:
    rows = [[c.strip() for c in l.strip().strip("|").split("|")]
            for l in lines if not _TABLE_SEP.match(l)]
    rows = [[_visible(inline(c)) for c in r] for r in rows]
    n = max(len(r) for r in rows)
    widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(n)]
    return "\n".join("  ".join((r[i] if i < len(r) else "").ljust(widths[i]) for i in range(n))
                     .rstrip() for r in rows)


def unglue_bullets(line: str) -> str:
    """'Aims: •One •Two' -> 'Aims:\n- One\n- Two'. A lone glyph mid-sentence
    isn't a list, so it's left alone."""
    if _ITEM.match(line) and not _ITEM.match(line).group(3):
        return line  # already a Markdown list item
    marks = list(_GLUED_BULLET.finditer(line))
    if not marks or (len(marks) == 1 and line[:marks[0].start()].strip()):
        return line
    indent = line[:len(line) - len(line.lstrip())]
    parts = [line[:marks[0].start()].strip()]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        parts.append(f"{indent}- {line[m.end():end].strip()}")
    return "\n".join(p for p in parts if p.strip() not in ("", "-"))


def chunk_blocks(chunk: str, para: int, px: int | None = None,
                 width: int | None = None) -> list[dict[str, Any]]:
    lines = chunk.split("\n")
    fence = _FENCE.match(lines[0])
    if fence:
        body = lines[1:]
        if body and body[-1].strip().startswith(fence.group(1)[0] * 3):
            body = body[:-1]
        code = textwrap.dedent("\n".join(body).expandtabs(4)).strip("\n")
        return [{"t": "code", "text": code, "lang": fence.group(2), "para": para}]
    if _is_math_open(lines[0].strip()):
        tex = _strip_math_delims(chunk)
        return [b for row in math_rows(tex) for b in _math_blocks(row, para, px, width)]

    blocks: list[dict[str, Any]] = []
    prose: list[str] = []
    quote: list[str] = []
    table: list[str] = []
    item: dict[str, Any] | None = None
    item_text: list[str] = []
    counters: dict[int, int] = {}

    def text_block(kind: str, text: str, **extra: Any) -> dict[str, Any]:
        images: list[dict[str, Any]] = []
        block = {"t": kind, "m": inline(text, images, px), "para": para, **extra}
        if images:
            block["img"] = images
        return block

    def flush() -> None:
        nonlocal item
        if prose:
            blocks.append(text_block("p", " ".join(prose)))
            prose.clear()
        if quote:
            blocks.append(text_block("note", " ".join(quote)))
            quote.clear()
        if table:
            blocks.append({"t": "code", "text": _table_text(table), "lang": "table",
                           "para": para})
            table.clear()
        if item is not None:
            blocks.append(text_block("li", " ".join(item_text), **item))
            item = None
            item_text.clear()

    # "intro •one •two" (PDF bullets run together or glued on) -> separate items.
    lines = [part for line in lines for part in unglue_bullets(line).split("\n")]
    for line in lines:
        if not line.strip():
            continue
        if m := _HEADING.match(line):
            flush()
            level = min(len(m.group(1)), 3)
            blocks.append(text_block(f"h{level}", m.group(2)))
        elif _RULE.match(line):
            flush()
            blocks.append({"t": "hr", "para": para})
        elif _TABLE.match(line):
            if not table:
                flush()
            table.append(line)
        elif m := _QUOTE.match(line):
            if not quote:
                flush()
            quote.append(m.group(1).strip())
        elif m := _ITEM.match(line):
            flush()
            level = min(len(m.group(1).expandtabs(4)) // 2, 3)
            marker = m.group(2) or "-"
            if marker[0].isdigit():
                counters[level] = int(marker[:-1])
                mark = f"{counters[level]}."
            else:
                mark = BULLETS[min(level, len(BULLETS) - 1)]
            item = {"lvl": level, "mark": mark}
            item_text.append(m.group(4).strip())
        elif item is not None:
            item_text.append(line.strip())  # continuation of the list item
        elif quote:
            quote.append(line.strip())
        else:
            prose.append(line.strip())
    flush()
    return blocks


def to_blocks(md: str, px: int | None = None,
              width: int | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for i, chunk in enumerate(paragraphs(md)):
        blocks.extend(chunk_blocks(chunk, i, px, width))
    return blocks


def doc(md: str, px: int | None = None, width: int | None = None) -> dict[str, Any]:
    """px: the reader's text size; width: its page width (both in device pixels)."""
    return {"px": px, "blocks": to_blocks(md, px, width)}


class Doc:
    """Builder for server-composed pages (results, progress)."""

    def __init__(self, px: int | None = None, width: int | None = None):
        self.px = px
        self.width = width
        self.blocks: list[dict[str, Any]] = []

    def h(self, text: str, level: int = 3) -> "Doc":
        self.blocks.append({"t": f"h{level}", "m": inline(text)})
        return self

    def p(self, markup: str) -> "Doc":
        self.blocks.append({"t": "p", "m": markup})
        return self

    def li(self, markup: str, level: int = 0) -> "Doc":
        self.blocks.append({"t": "li", "lvl": level, "mark": BULLETS[min(level, 2)],
                            "m": markup})
        return self

    def note(self, markup: str) -> "Doc":
        self.blocks.append({"t": "note", "m": markup})
        return self

    def md(self, text: str) -> "Doc":
        self.blocks.extend(to_blocks(text, self.px, self.width))
        return self

    def hr(self) -> "Doc":
        self.blocks.append({"t": "hr"})
        return self

    def build(self) -> dict[str, Any]:
        return {"px": self.px, "blocks": self.blocks}


# --- plain text (offline agents, prompts, dashboard) -----------------------------------------


def plain_inline(text: str) -> str:
    return markup_to_plain(inline(text))


def prose(md: str) -> list[str]:
    """Readable prose paragraphs only (no code or display maths), as plain
    Unicode text. List items become separate entries."""
    out: list[str] = []
    for chunk in paragraphs(md):
        kind = kind_of(chunk)
        if kind in ("code", "math"):
            continue
        for b in chunk_blocks(chunk, 0, None):
            if b["t"] in ("p", "li", "note", "h1", "h2", "h3"):
                text = markup_to_plain(b["m"]).strip()
                if text:
                    out.append(text)
    return out

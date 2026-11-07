"""Markdown + LaTeX -> Kindle blocks, and the API surfaces that carry them."""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import random
from importlib import resources

import pytest
from fastapi.testclient import TestClient

from edukindle import config, mathrender, richtext
from edukindle.api import create_app
from edukindle.richtext import inline, markup_to_plain, paragraphs, tex_to_markup, to_blocks

SAMPLE = "calculus-i-derivatives"
MONO = f'<span font_family="{richtext.MONO}">'
THIN = " "


@pytest.mark.parametrize("tex, plain", [
    (r"f'(a) = \lim_{h \to 0} \frac{f(a+h) - f(a)}{h}",
     f"f′(a) = lim_(h→0){THIN}(f(a + h) − f(a))/h"),
    (r"\frac{1}{2}x^2 + 3x - 5 \le 0", "¹⁄₂x² + 3x − 5 ≤ 0"),
    (r"\sqrt{x^2+y^2}", "√(x² + y²)"),
    (r"\sqrt[3]{8} = 2", "³√8 = 2"),
    (r"\sin x \cdot \cos(x)", f"sin{THIN}x · cos(x)"),
    (r"e^{-x}", "e^(−x)"),
    (r"\int_0^1 x^2\,dx", f"∫₀¹{THIN}x²{THIN}dx"),
    (r"90^\circ", "90°"),
    (r"f''(x)", "f″(x)"),
    (r"\frac{d}{dx} x^n = n x^{n-1}", f"d/dx{THIN}xⁿ = nxⁿ⁻¹"),
    (r"a - b, -a", "a − b, −a"),
    (r"\alpha \to \infty", "α → ∞"),
    (r"\mathbb{R}^n", "ℝⁿ"),
    (r"f(x) = \begin{cases} x^2 & x \ge 0 \\ -x & x < 0 \end{cases}",
     "f(x) = { x², x ≥ 0; −x, x < 0 }"),
])
def test_tex_to_markup(tex, plain):
    markup = tex_to_markup(tex)
    assert richtext.well_formed(markup)
    assert markup_to_plain(markup) == plain


def test_variables_are_italic_functions_upright():
    assert tex_to_markup(r"\sin x") == f"sin{THIN}<i>x</i>"
    assert tex_to_markup("dx") == "<i>dx</i>"
    assert tex_to_markup(r"\hat{x}") == "<i>x̂</i>"  # accent inside the italic run


def test_inline_markdown():
    assert inline("**bold** and *it* and ~~no~~") == "<b>bold</b> and <i>it</i> and <s>no</s>"
    assert inline("call `f(x)` now") == f"call {MONO}f(x)</span> now"
    assert inline("a < b & c") == "a &lt; b &amp; c"
    assert inline("x is $x^2$") == "x is <i>x</i><sup>2</sup>"
    assert inline(r"\(a+b\)") == "<i>a</i> + <i>b</i>"
    assert inline("[docs](http://x.y)") == "<u>docs</u>"


def test_inline_leaves_prices_and_identifiers_alone():
    assert inline("costs $5 and $10 today") == "costs $5 and $10 today"
    assert inline("snake_case_name and 2 * 3 * 4") == "snake_case_name and 2 * 3 * 4"
    assert inline(r"literal \$ sign") == "literal $ sign"


def test_inline_markup_is_always_well_formed():
    rng = random.Random(7)
    alphabet = list("ab *_`$~[]()<>&\\^{}") + ["**", "$$", "\\frac", "\\left(", "\\begin{"]
    for _ in range(3000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24)))
        assert richtext.well_formed(inline(s)), s


MD = r"""Intro with $x^2$.

- one
- two with **bold**
  - nested
1. first
2. second

```python
def f(x):

    return x ** 2
```

$$
\frac{d}{dx} x^n = n x^{n-1}
$$

> **Tip:** remember the chain rule.

| a | b |
|---|---|
| 1 | 22 |

### Heading
After heading."""


def test_paragraphs_keep_code_and_maths_whole():
    chunks = paragraphs(MD)
    assert len(chunks) == 7
    assert chunks[2] == "```python\ndef f(x):\n\n    return x ** 2\n```"  # blank line kept
    assert chunks[3].startswith("$$") and chunks[3].endswith("$$")


def test_blocks():
    blocks = to_blocks(MD)
    kinds = [(b["t"], b["para"]) for b in blocks]
    assert kinds == [("p", 0), ("li", 1), ("li", 1), ("li", 1), ("li", 1), ("li", 1),
                     ("code", 2), ("math", 3), ("note", 4), ("code", 5), ("h3", 6), ("p", 6)]
    items = [b for b in blocks if b["t"] == "li"]
    assert [(b["lvl"], b["mark"]) for b in items] == [
        (0, "•"), (0, "•"), (1, "◦"), (0, "1."), (0, "2.")]
    code = blocks[6]
    assert code["text"] == "def f(x):\n\n    return x ** 2" and code["lang"] == "python"
    assert blocks[7]["m"] and "png" not in blocks[7]  # no size given -> markup only
    assert blocks[8]["m"] == "<b>Tip:</b> remember the chain rule."
    assert blocks[9]["text"] == "a  b\n1  22"  # tables become aligned monospace


def test_display_maths_png():
    b64, w, h, depth = mathrender.render(r"\frac{a}{b}", 42)
    png = base64.b64decode(b64)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and w > 0 and h > 40 and 0 < depth < h
    assert mathrender.render(r"\begin{cases} a \end{cases}", 42) is None
    assert mathrender.render(r"\frac{", 42) is None  # unparseable -> caller uses text
    [blk] = to_blocks(r"$$\begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}$$", px=42)
    assert "png" not in blk and blk["m"] == "(1 0; 0 1)"


def test_wide_equations_wrap_at_relations():
    tex = (r"\frac{d}{dx}\tan x = \frac{\cos x \cdot \cos x - \sin x \cdot (-\sin x)}"
           r"{\cos^2 x} = \frac{1}{\cos^2 x} = \sec^2 x")
    [one] = to_blocks(f"$${tex}$$", px=42)
    rows = to_blocks(f"$${tex}$$", px=42, width=700)
    assert one["w"] > 700
    assert len(rows) >= 2 and all(r["w"] <= 700 for r in rows[1:])
    assert all(markup_to_plain(r["m"]).startswith("=") for r in rows[1:])


def test_aligned_rows_split():
    rows = to_blocks(r"$$\begin{aligned} a &= b \\ &= c \end{aligned}$$", px=40)
    assert [markup_to_plain(r["m"]) for r in rows] == ["a = b", "= c"]
    assert all("png" in r for r in rows)


def test_normalise_keeps_structure():
    assert richtext.normalise("a   long\nline") == "a long line"
    assert richtext.normalise("- one  item\n  - two") == "- one item\n  - two"
    assert richtext.normalise("```\n  x = 1  \n```") == "```\n  x = 1\n```"


def test_prose_is_plain_text():
    assert richtext.prose(MD) == ["Intro with x².", "one", "two with bold", "nested",
                                  "first", "second", "Tip: remember the chain rule.",
                                  "Heading", "After heading."]


def test_sample_json_is_valid_markdown():
    data = json.loads(resources.files("edukindle.sample")
                      .joinpath("calculus_derivatives.json").read_text())
    for sec in data["sections"]:
        for chunk in sec["paragraphs"]:
            for b in to_blocks(chunk):
                assert richtext.well_formed(b.get("m", "")), (sec["title"], chunk)


# --- API ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    cfg = dataclasses.replace(config.load(), data_dir=tmp_path, api_key=None, offline=True)
    with TestClient(create_app(cfg), headers={"X-EK-Px": "40", "X-EK-Width": "900"}) as c:
        yield c


def test_quiz_markup_and_results_doc(client):
    quiz = client.post("/api/quiz", json={"section_id": f"{SAMPLE}.3"}).json()
    for q in quiz["questions"]:
        assert richtext.well_formed(q["prompt_markup"])
        assert len(q["options_markup"]) == 4
    stored = client.app.state.svc.store.quizzes[quiz["quiz_id"]]
    wrong = {q["id"]: (q["correct"] + 1) % 4 for q in stored["questions"]}
    res = client.post(f"/api/quiz/{quiz['quiz_id']}/answers", json={"answers": wrong}).json()
    kinds = [b["t"] for b in res["doc"]["blocks"]]
    assert kinds.count("h3") == 4  # one per question + "Mastery"
    assert "note" in kinds and "li" in kinds
    assert all(r["hint_markup"] for r in res["results"])
    assert all(richtext.well_formed(b["m"]) for b in res["doc"]["blocks"] if "m" in b)


def test_progress_doc(client):
    prog = client.get(f"/api/progress/{SAMPLE}").json()
    heads = [b["m"] for b in prog["doc"]["blocks"] if b["t"] == "h3"]
    assert heads[0] == "Curriculum Architect" and "All concepts" in heads
    assert "coach" in prog and "concepts" in prog  # dashboard fields still there


def test_explain_paragraph_index_counts_code_blocks_whole(client):
    md = ("# Code\n\n## Loops\n\nIntro paragraph " + "word " * 60 + "\n\n```python\nfor i in "
          "range(3):\n\n    print(i)\n```\n\nAfter the code " + "word " * 60)
    doc_id = client.post("/api/documents/upload", files={
        "file": ("code.md", io.BytesIO(md.encode()), "text/markdown")}).json()["id"]
    sec = client.get(f"/api/sections/{doc_id}.1").json()
    code = next(b for b in sec["doc"]["blocks"] if b["t"] == "code")
    assert code["text"] == "for i in range(3):\n\n    print(i)"
    out = client.post("/api/explain", json={"section_id": f"{doc_id}.1", "mode": "paragraph",
                                            "paragraph": code["para"]}).json()
    assert "print(i)" in out["body"]  # the offline agent quotes the tapped code block
    assert any(b["t"] == "code" for b in out["doc"]["blocks"])


def test_sample_upgrade_keeps_ids_and_progress(tmp_path):
    cfg = dataclasses.replace(config.load(), data_dir=tmp_path, api_key=None, offline=True)
    with TestClient(create_app(cfg)) as c:
        svc = c.app.state.svc
        doc = svc.store.documents[SAMPLE]
        assert doc["version"] == 2
        # Pretend this is an old v1 copy with some progress on it.
        doc["version"] = 1
        doc["sections"][0]["text"] = "old plain text"
        svc.store.save_document(doc)
        svc.store.student["sections"][f"{SAMPLE}.1"] = {"passed": True, "best": 3,
                                                       "attempts": 1}
        svc.store.save_student()
    with TestClient(create_app(cfg)) as c:
        svc = c.app.state.svc
        doc = svc.store.documents[SAMPLE]
        assert doc["version"] == 2 and "$$" in doc["sections"][0]["text"]
        assert [s["id"] for s in doc["sections"]] == [f"{SAMPLE}.{i}" for i in range(1, 8)]
        assert c.get(f"/api/documents/{SAMPLE}").json()["sections"][0]["status"] == "passed"
        assert len(svc.store.documents) == 1  # upgraded in place, not duplicated


# --- PDF clean-up at render time ---------------------------------------------------


@pytest.mark.parametrize("line, out", [
    ("•Understand pseudocode •Understand iteration",
     "- Understand pseudocode\n- Understand iteration"),
    ("Aims: •One •Two", "Aims:\n- One\n- Two"),
    ("•Introduction to Algorithms", "- Introduction to Algorithms"),
    ("a lone • mid-sentence stays", "a lone • mid-sentence stays"),
    ("h◦ (g◦f) =ext (h◦g)◦f", "h◦ (g◦f) =ext (h◦g)◦f"),  # ◦ is composition, not a bullet
    ("- already an item", "- already an item"),
])
def test_unglue_bullets(line, out):
    assert richtext.unglue_bullets(line) == out


def test_glued_bullets_render_as_list_items():
    blocks = to_blocks("Objectives •Understand pseudocode •Establish closed forms")
    assert [(b["t"], b["m"]) for b in blocks] == [
        ("p", "Objectives"), ("li", "Understand pseudocode"), ("li", "Establish closed forms")]


def test_unicode_maths_letters_become_italics():
    # 𝑇(𝑛) uses plane-1 letters that no Kindle font has.
    assert inline("𝑇(𝑛) = 𝑛 + 𝑇(𝑛 − 1)") == "<i>T</i>(<i>n</i>) = <i>n</i> + <i>T</i>(<i>n</i> − 1)"
    assert inline("𝐀𝐁 and ℎ") == "<b>A</b><b>B</b> and <i>h</i>"
    assert inline("x︂ ⎪y⎪") == "x y"  # PDF debris removed


def test_complex_inline_maths_becomes_an_image():
    images: list = []
    markup = inline(r"so $\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$ and $x^2$", images, 42)
    assert markup == f"so {richtext.OBJECT} and <i>x</i><sup>2</sup>"
    [img] = images
    assert img["w"] > img["h"] > img["d"] > 0
    assert img["h"] < 100  # limits sit beside the sum in running text
    # Without a list to fill (quiz labels), the same maths stays text.
    assert richtext.OBJECT not in inline(r"$\frac{a}{b}$")
    [block] = to_blocks(r"Here $\frac{a}{b}$ is a fraction.", px=42)
    assert block["m"].count(richtext.OBJECT) == len(block["img"]) == 1


def test_pdf_bullets_parsed_at_upload():
    from edukindle import ingest
    page = ("Learning objectives\n"
            "•Understand pseudocode for algorithm description •Understand the design\n"
            "of iterative algorithms\n"
            "•Establish closed-forms\n"
            "Some ordinary prose that wraps onto a second line without a full stop and\n"
            "finishes here.\n")
    assert [t for _, t in ingest._pdf_paragraphs(page)] == [
        "Learning objectives",
        "- Understand pseudocode for algorithm description",
        "- Understand the design of iterative algorithms",
        "- Establish closed-forms",
        "Some ordinary prose that wraps onto a second line without a full stop and finishes here.",
    ]

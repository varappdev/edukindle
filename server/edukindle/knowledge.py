"""Knowledge graph of syllabus concepts and per-concept mastery.

Concepts are nodes; an edge A -> B means "A is a prerequisite of B". Mastery is
tracked with Bayesian Knowledge Tracing (BKT), which gives a probability that
the student has learned a concept from a sequence of right/wrong answers while
allowing for lucky guesses and careless slips.
"""

from __future__ import annotations

from html import escape
from typing import Any

import networkx as nx

# -- Bayesian Knowledge Tracing ---------------------------------------------

P_INIT = 0.25     # prior that a concept is already known
P_LEARN = 0.15    # chance of learning it from one practice opportunity
P_SLIP = 0.10     # knows it but answers wrong
P_GUESS = 0.25    # doesn't know it but answers right (4 options)
P_GUESS_HINTED = 0.50  # a hint makes a correct answer weaker evidence

MASTERED = 0.80
LEARNING = 0.50


def bkt_update(p: float, correct: bool, hinted: bool = False) -> float:
    guess = P_GUESS_HINTED if hinted else P_GUESS
    if correct:
        num = p * (1 - P_SLIP)
        post = num / (num + (1 - p) * guess)
    else:
        num = p * P_SLIP
        post = num / (num + (1 - p) * (1 - guess))
    return post + (1 - post) * P_LEARN


def status_of(rec: dict[str, Any] | None) -> str:
    if not rec or rec.get("attempts", 0) == 0:
        return "unknown"
    p = rec["p"]
    if p >= MASTERED:
        return "mastered"
    if p >= LEARNING:
        return "learning"
    return "weak"


# -- Graph -------------------------------------------------------------------


def build_graph(doc: dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    order = {s["id"]: s["index"] for s in doc["sections"]}
    for cid, c in doc["concepts"].items():
        first = min((order.get(s, 1 << 30) for s in c["sections"]), default=1 << 30)
        g.add_node(cid, label=c["label"], order=first)
    for cid, c in doc["concepts"].items():
        for pre in c.get("prereqs", []):
            if pre in g and pre != cid:
                g.add_edge(pre, cid)
    # Prerequisite cycles can come out of the LLM; drop the back edge of each.
    while True:
        try:
            cycle = nx.find_cycle(g)
        except nx.NetworkXNoCycle:
            break
        u, v = max(cycle, key=lambda e: g.nodes[e[0]]["order"])[:2]
        g.remove_edge(u, v)
    return g


def layout(g: nx.DiGraph, max_cols: int = 4) -> dict[str, tuple[float, float]]:
    """Layered top-down layout: prerequisites above what they unlock.

    Rows are topological generations, wrapped at `max_cols` so it fits a
    portrait E-ink screen; within a row nodes are ordered by the mean x of their
    prerequisites (barycentre heuristic) to keep edge crossings down.
    """
    layer: dict[str, int] = {}
    for depth, gen in enumerate(nx.topological_generations(g)):
        for n in gen:
            layer[n] = depth
    # A root concept only needed deep in the graph (e.g. composition -> chain
    # rule) would otherwise sit in the top row with a long edge running behind
    # other nodes; drop it to just above the first concept it unlocks.
    for n in g.nodes:
        if g.in_degree(n) == 0 and g.out_degree(n) > 0:
            layer[n] = min(layer[s] for s in g.successors(n)) - 1

    rows: list[list[str]] = []
    for depth in sorted(set(layer.values())):
        gen = sorted((n for n in layer if layer[n] == depth),
                     key=lambda n: (g.nodes[n]["order"], n))
        for i in range(0, len(gen), max_cols):
            rows.append(gen[i:i + max_cols])

    pos: dict[str, tuple[float, float]] = {}
    for r, row in enumerate(rows):
        def bary(n: str) -> float:
            xs = [pos[p][0] for p in g.predecessors(n) if p in pos]
            return sum(xs) / len(xs) if xs else g.nodes[n]["order"] / 100
        if r:
            row.sort(key=bary)
        for c, n in enumerate(row):
            pos[n] = ((c + 0.5) / len(row), (r + 0.5) / len(rows))
    return pos


def root_gaps(g: nx.DiGraph, concepts: dict[str, Any], cid: str) -> list[str]:
    """Shallowest unmastered prerequisites of `cid` — the likely root causes."""
    out: list[str] = []
    seen = set()
    frontier = list(g.predecessors(cid)) if cid in g else []
    while frontier:
        nxt = []
        for p in frontier:
            if p in seen:
                continue
            seen.add(p)
            if status_of(concepts.get(p)) in ("weak", "unknown"):
                out.append(p)
            else:
                nxt.extend(g.predecessors(p))
        frontier = nxt
    return out


def graph_view(doc: dict[str, Any], mastery: dict[str, Any]) -> dict[str, Any]:
    g = build_graph(doc)
    pos = layout(g)
    nodes = []
    for cid in g.nodes:
        c = doc["concepts"][cid]
        rec = mastery.get(cid)
        x, y = pos[cid]
        nodes.append({
            "id": cid,
            "label": c["label"],
            "description": c.get("description", ""),
            "sections": c["sections"],
            "mastery": round(rec["p"], 3) if rec else 0.0,
            "attempts": rec["attempts"] if rec else 0,
            "status": status_of(rec),
            "x": round(x, 4),
            "y": round(y, 4),
        })
    rows = len({n["y"] for n in nodes})
    return {
        "doc_id": doc["id"],
        "title": doc["title"],
        "rows": rows,
        "nodes": nodes,
        "edges": [{"source": u, "target": v} for u, v in g.edges],
    }


def graph_svg(view: dict[str, Any], width: int = 960) -> str:
    """Server-rendered SVG for the web dashboard, same look as the Kindle map."""
    rows = max(view["rows"], 1)
    height = rows * 120 + 20
    by_id = {n["id"]: n for n in view["nodes"]}
    cols_in_row: dict[float, int] = {}
    for n in view["nodes"]:
        cols_in_row[n["y"]] = cols_in_row.get(n["y"], 0) + 1
    bw = min(200, width / max(cols_in_row.values(), default=1) - 24)
    bh = 64

    def xy(n):
        return n["x"] * width, n["y"] * (height - 20) + 10

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="Knowledge graph" class="kg">',
        '<defs><marker id="arr" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" '
        'fill="currentColor"/></marker></defs>',
    ]
    for e in view["edges"]:
        a, b = by_id[e["source"]], by_id[e["target"]]
        (x1, y1), (x2, y2) = xy(a), xy(b)
        out.append(f'<line x1="{x1:.0f}" y1="{y1 + bh / 2:.0f}" x2="{x2:.0f}" '
                   f'y2="{y2 - bh / 2 - 2:.0f}" stroke="currentColor" stroke-width="2" '
                   f'marker-end="url(#arr)"/>')
    for n in view["nodes"]:
        x, y = xy(n)
        st = n["status"]
        fill = "var(--ink)" if st == "mastered" else "var(--paper)"
        text = "var(--paper)" if st == "mastered" else "var(--ink)"
        sw = {"mastered": 3, "learning": 3, "weak": 4, "unknown": 1.5}[st]
        dash = ' stroke-dasharray="8 5"' if st == "weak" else (
            ' stroke-dasharray="2 4"' if st == "unknown" else "")
        pct = round(n["mastery"] * 100)
        out.append(f'<g><title>{escape(n["label"])}: {pct}% ({st})</title>')
        out.append(f'<rect x="{x - bw / 2:.0f}" y="{y - bh / 2:.0f}" width="{bw:.0f}" '
                   f'height="{bh}" rx="8" fill="{fill}" stroke="var(--ink)" '
                   f'stroke-width="{sw}"{dash}/>')
        out.append(f'<text x="{x:.0f}" y="{y - 4:.0f}" text-anchor="middle" fill="{text}" '
                   f'font-size="15" font-weight="700">{escape(_clip(n["label"], 24))}</text>')
        # Mastery bar: flat fill, no gradients.
        bx, by_, bwid = x - bw / 2 + 14, y + 12, bw - 28
        out.append(f'<rect x="{bx:.0f}" y="{by_:.0f}" width="{bwid:.0f}" height="8" '
                   f'fill="none" stroke="{text}" stroke-width="1.5"/>')
        out.append(f'<rect x="{bx:.0f}" y="{by_:.0f}" width="{bwid * n["mastery"]:.0f}" '
                   f'height="8" fill="{text}"/></g>')
    out.append("</svg>")
    return "".join(out)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"

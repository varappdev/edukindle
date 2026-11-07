"""Server-rendered web dashboard: library, mastery and the knowledge graph.

Plain HTML + a few lines of JS that re-fetch the live panel every few seconds,
so a teacher (or the student) can watch the map fill in while studying on the
Kindle.
"""

from __future__ import annotations

import time
from html import escape
from typing import Any

from . import knowledge

STATUS_TEXT = {"mastered": "Mastered", "learning": "Learning", "weak": "Weak",
               "unknown": "Not tested"}

CSS = """
:root{--paper:#fff;--ink:#000;--mute:#555;--rule:#000;--soft:#eee;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#111;--ink:#f2f2f2;--mute:#aaa;
--rule:#f2f2f2;--soft:#222;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{border-bottom:3px solid var(--rule);padding:14px 20px;display:flex;
align-items:center;gap:16px;flex-wrap:wrap}
header h1{font-size:22px;margin:0;letter-spacing:.02em}
.pill{border:2px solid var(--ink);border-radius:999px;padding:2px 10px;font-size:13px;
font-weight:600}
.pill.on{background:var(--ink);color:var(--paper)}
main{max-width:1180px;margin:0 auto;padding:16px 20px 60px}
nav.docs{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 18px}
nav.docs a{border:2px solid var(--ink);padding:6px 12px;border-radius:6px;color:inherit;
text-decoration:none;font-weight:600}
nav.docs a.cur{background:var(--ink);color:var(--paper)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;
margin-bottom:18px}
.stat{border:2px solid var(--ink);border-radius:8px;padding:10px 14px}
.stat b{display:block;font-size:30px;line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{color:var(--mute);font-size:13px;text-transform:uppercase;letter-spacing:.06em}
.grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:18px}
@media (max-width:860px){.grid{grid-template-columns:1fr}}
section.card{border:2px solid var(--ink);border-radius:8px;padding:14px 16px;margin-bottom:18px}
section.card h2{font-size:15px;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px}
.kg{color:var(--ink);display:block;max-width:100%;height:auto}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;margin-top:8px}
.legend i{display:inline-block;width:22px;height:12px;border:2px solid var(--ink);
vertical-align:-1px;margin-right:5px;border-radius:3px}
.legend .m{background:var(--ink)}.legend .w{border-style:dashed;border-width:3px}
.legend .u{border-style:dotted;border-width:2px}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{text-align:left;padding:6px 4px;border-bottom:1px solid var(--soft)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute)}
.bar{width:120px;height:10px;border:1.5px solid var(--ink);display:inline-block;
vertical-align:middle}
.bar>i{display:block;height:100%;background:var(--ink)}
.num{font-variant-numeric:tabular-nums;text-align:right}
.gap{margin:0 0 10px}.gap b{display:block}
.gap small{color:var(--mute)}
.coach{font-size:16px;margin:0}
.muted{color:var(--mute)}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 10px}
input[type=file],input[type=text],textarea{font:inherit;color:inherit;background:var(--paper);
border:2px solid var(--ink);border-radius:6px;padding:6px}
textarea{width:100%;min-height:90px}
button{font:inherit;font-weight:700;background:var(--ink);color:var(--paper);
border:2px solid var(--ink);border-radius:6px;padding:7px 14px;cursor:pointer}
button.ghost{background:var(--paper);color:var(--ink)}
"""

JS = """
const live=document.getElementById('live');
async function tick(){
  try{const r=await fetch('/dashboard/live'+location.search);
      if(r.ok) live.innerHTML=await r.text();}catch(e){}
}
setInterval(tick,4000);
async function api(method,url,body){
  const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},
    body:body?JSON.stringify(body):undefined});
  if(!r.ok){alert((await r.json()).error||r.statusText);return null}
  return r.json();
}
document.getElementById('paste')?.addEventListener('submit',async e=>{
  e.preventDefault();const f=e.target;
  const out=await api('POST','/api/documents',{title:f.doctitle.value,text:f.text.value});
  if(out) location.href='/?doc='+out.id;
});
document.getElementById('reset')?.addEventListener('click',async()=>{
  if(confirm('Reset all learner progress?')){await api('POST','/api/reset');tick();}
});
document.getElementById('delete')?.addEventListener('click',async e=>{
  if(confirm('Delete this document?')){
    await api('DELETE','/api/documents/'+e.target.dataset.doc);location.href='/';}
});
"""


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _ago(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 5400:
        return f"{round(d / 60)} min ago"
    if d < 129600:
        return f"{round(d / 3600)} h ago"
    return f"{round(d / 86400)} d ago"


def _pick(svc, doc_id: str | None) -> dict[str, Any] | None:
    docs = svc.store.documents
    if doc_id in docs:
        return docs[doc_id]
    return next(iter(docs.values()), None)


def live(svc, doc_id: str | None) -> str:
    doc = _pick(svc, doc_id)
    if not doc:
        return '<p class="muted">No documents yet. Upload a PDF or paste some text below.</p>'
    prog = svc.progress(doc["id"])
    view = svc.graph(doc["id"])
    c = prog["counts"]

    out = ['<div class="stats">']
    for val, label in [
        (_pct(prog["overall"]), "Overall mastery"),
        (f'{prog["sections_passed"]}/{prog["sections_total"]}', "Sections passed"),
        (c["mastered"], "Concepts mastered"),
        (c["weak"], "Weak concepts"),
        (len(prog["history"]), "Recent checks"),
    ]:
        out.append(f'<div class="stat"><b>{val}</b><span>{label}</span></div>')
    out.append('</div><div class="grid"><div>')

    out.append('<section class="card"><h2>Knowledge graph</h2>')
    if not doc["concepts_ready"]:
        out.append('<p class="muted">The Curriculum Architect is mapping this document…</p>')
    else:
        out.append(knowledge.graph_svg(view))
        out.append('<div class="legend"><span><i class="m"></i>Mastered</span>'
                   '<span><i></i>Learning</span><span><i class="w"></i>Weak</span>'
                   '<span><i class="u"></i>Not tested</span>'
                   '<span class="muted">Arrows point from prerequisite to the concept it '
                   'unlocks. Bars show mastery.</span></div>')
    out.append('</section>')

    out.append('<section class="card"><h2>Concept mastery</h2><table><tr><th>Concept</th>'
               '<th>Status</th><th>Mastery</th><th class="num">Answers</th></tr>')
    for cc in sorted(prog["concepts"], key=lambda x: -x["mastery"]):
        out.append(f'<tr><td>{escape(cc["label"])}</td><td>{STATUS_TEXT[cc["status"]]}</td>'
                   f'<td><span class="bar"><i style="width:{_pct(cc["mastery"])}"></i></span> '
                   f'{_pct(cc["mastery"])}</td><td class="num">{cc["attempts"]}</td></tr>')
    out.append('</table></section></div><div>')

    out.append('<section class="card"><h2>Curriculum Architect</h2>'
               f'<p class="coach">{escape(prog["coach"])}</p></section>')

    out.append('<section class="card"><h2>Knowledge gaps</h2>')
    if not prog["weak"]:
        out.append('<p class="muted">No gaps detected yet.</p>')
    for g in prog["weak"]:
        roots = ", ".join(escape(r["label"]) for r in g["root_causes"])
        out.append(f'<div class="gap"><b>{escape(g["label"])} · {_pct(g["mastery"])}</b>'
                   + (f'<small>Likely root cause: {roots}</small>' if roots else
                      '<small>No weaker prerequisite found: practise this directly.</small>')
                   + '</div>')
    out.append('</section>')

    out.append('<section class="card"><h2>Recent checks</h2>')
    if not prog["history"]:
        out.append('<p class="muted">No quizzes taken yet.</p>')
    else:
        out.append('<table><tr><th>Section</th><th class="num">Score</th><th>When</th></tr>')
        for h in prog["history"]:
            mark = " ✓" if h["ready"] else ""
            out.append(f'<tr><td>{escape(h["section_title"])}</td>'
                       f'<td class="num">{h["score"]}/{h["total"]}{mark}</td>'
                       f'<td class="muted">{_ago(h["ts"])}</td></tr>')
        out.append('</table>')
    out.append('</section></div></div>')
    return "".join(out)


def page(svc, doc_id: str | None) -> str:
    doc = _pick(svc, doc_id)
    llm = svc.llm
    tabs = "".join(
        f'<a href="/?doc={escape(d["id"])}" class="{"cur" if doc and d["id"] == doc["id"] else ""}">'
        f'{escape(d["title"])}</a>' for d in svc.store.documents.values())
    status = (f'<span class="pill on">OpenAI · {escape(llm.model)}</span>' if llm.enabled
              else '<span class="pill">Offline agents (no OPENAI_API_KEY)</span>')
    delete = (f'<button class="ghost" type="button" id="delete" data-doc="{escape(doc["id"])}">'
              'Delete this document</button>' if doc else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EduKindle Dashboard</title><style>{CSS}</style></head><body>
<header><h1>EduKindle</h1>{status}<span class="muted">Study companion dashboard</span></header>
<main>
<nav class="docs">{tabs}</nav>
<div id="live">{live(svc, doc["id"] if doc else None)}</div>
<section class="card"><h2>Library</h2>
<form action="/dashboard/upload" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".pdf,.txt,.md" required>
<button>Upload PDF / text</button></form>
<form id="paste" style="display:block">
<input type="text" name="doctitle" placeholder="Title" required style="margin-bottom:8px">
<textarea name="text" placeholder="Paste chapter text. Markdown '## headings' become sections."
required></textarea><div style="margin-top:8px"><button>Add pasted text</button></div></form>
<div style="display:flex;gap:8px;flex-wrap:wrap">{delete}<button class="ghost" type="button" id="reset">Reset learner progress</button></div>
</section></main><script>{JS}</script></body></html>"""

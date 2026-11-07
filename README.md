# EduKindle

A distraction-free AI study companion for jailbroken Kindles. Read a textbook
chapter on E-ink, tap a paragraph for a plain-language explanation, take a
3-question check at the end of each section, and get Socratic hints and a
personal knowledge map. No notifications and no tabs.

```
┌──────────── Kindle (C / GTK 2) ────────────┐        ┌──────────── Server (Python / FastAPI) ────────────┐
│ Library → Contents → Reader (paginated)    │  HTTP  │ Agent 1  Socratic Educator                        │
│   Summary · Simplify · Explain ¶ · Check   │ ◀────▶ │   quizzes · explanations · grading · readiness    │
│ Quiz → Results → Retry with hints          │  JSON  │ Agent 2  Curriculum Architect                     │
│ Progress · Knowledge map (Cairo)           │        │   concept graph (NetworkX) · BKT mastery · plan   │
└────────────────────────────────────────────┘        │ Web dashboard  http://<server>:8080/              │
                                                      └───────────────── OpenAI API ──────────────────────┘
```

## Quick start

### 1. Server (your laptop, same Wi-Fi as the Kindle)

```sh
cd server
uv sync
export OPENAI_API_KEY=sk-...      # or OPENROUTER_API_KEY=sk-or-...; with neither, heuristic "offline" agents are used
uv run python -m edukindle         # prints the dashboard URL and the line for edukindle.conf
```

Open the printed dashboard URL to upload PDFs, Markdown or text, and to watch
the knowledge graph fill in. A sample *Calculus I: Derivatives* text with a
hand-written question bank is loaded on first run.

| Env var | Default | |
|---|---|---|
| `OPENAI_API_KEY` | unset | Use OpenAI for the agents |
| `OPENROUTER_API_KEY` | unset | Use OpenRouter instead (picked automatically when it's the only key) |
| `EDUKINDLE_PROVIDER` | auto | `openai` or `openrouter`, to choose when both keys are set |
| `EDUKINDLE_MODEL` | `gpt-5-mini` / `openai/gpt-5-mini` | Any chat-completions model; OpenRouter IDs look like `anthropic/claude-haiku-4.5` |
| `EDUKINDLE_REASONING` | `low` | Reasoning effort; set empty for non-reasoning models |
| `OPENAI_BASE_URL` | OpenAI | Any other OpenAI-compatible endpoint (provider `openai`) |
| `EDUKINDLE_PORT` / `EDUKINDLE_HOST` | `8080` / `0.0.0.0` | |
| `EDUKINDLE_DATA` | `./data` | Documents, learner state, cache (JSON files) |
| `EDUKINDLE_OFFLINE` | unset | `1` forces the offline agents |

### 2. Kindle app

Cross-compile (toolchain setup: **[docs/SETUP.md](docs/SETUP.md)**):

```sh
cmake --preset kindlehf && cmake --build --preset kindlehf
cmake --install build/kindlehf --prefix dist --strip
cp -r dist/extensions/edukindle /run/media/$USER/Kindle/extensions/
```

On first launch `run.sh` creates `extensions/edukindle/edukindle.conf` from the
example. Set `server=<laptop-ip>:8080` in it. Launch from **KUAL → EduKindle**.
Logs go to `/tmp/edukindle.log`.

### 3. Desktop build (development)

The same app runs on a Linux desktop with GTK 2:

```sh
cmake --preset host && cmake --build --preset host
build/host/edukindle --server 127.0.0.1:8080            # Paperwhite-shaped window
build/host/edukindle --server 127.0.0.1:8080 --size 600x800
ctest --test-dir build/host                              # JSON + HTTP client tests
cd server && uv run pytest                               # server tests
```

Arrow keys, Page Up/Down and Space turn pages; Esc goes back.

## How it works

**Reading.** Sections are paginated with Pango, not scrolled, which suits
E-ink. Tap the right two thirds of the page to go forward and the left third to
go back, or swipe. *Summary* and *Simplify* explain the whole section.
*Explain ¶* turns the next tap into a paragraph selection.

**Assessment (Agent 1, Socratic Educator).** Each check has 3 multiple-choice
questions written for the section and pitched at the student's current mastery
of its concepts. Every wrong option comes with a note on the misconception
behind it and a Socratic hint, so grading is instant. A missed question can be
retried once with the hint shown. After that the answer is revealed. The
student is *ready to advance* with at least 2/3 right first time and no tested
concept still weak.

**Knowledge graph (Agent 2, Curriculum Architect).** On upload, the Architect
maps the document onto syllabus concepts and prerequisite edges (a NetworkX
DiGraph, with cycles removed). Mastery per concept is tracked with Bayesian
Knowledge Tracing, and a correct answer after a hint counts as weaker evidence.
When a student misses a question, the Architect walks the graph upwards to find
the unmastered prerequisites behind it. It then recommends the next step
(advance, re-read, or review a root-cause section) and writes a short coach
note. The graph is drawn natively on the Kindle with Cairo and as SVG on the
web dashboard. Solid nodes are mastered, heavy outlines are learning, dashed are
weak and dotted are not yet tested.

**Latency.** OpenAI calls stay off the interactive path where possible.
Opening a section prefetches its quiz. Serving a quiz prefetches the next one.
Grading needs no model call. Explanations are cached. Coach notes refresh in
the background. Background jobs are single-flight, so repeated taps never
duplicate requests. Non-LLM endpoints respond in about 1 ms.

**No auth.** The API is open by design, and the server keeps one learner
profile. Run it on a trusted network.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/home` | Library, last-read section, LLM status |
| GET | `/api/documents/{doc}` | Table of contents with progress |
| POST | `/api/documents` | `{title, text}` (Markdown headings become sections) |
| POST | `/api/documents/upload` | Multipart PDF / `.md` / `.txt` |
| DELETE | `/api/documents/{doc}` | |
| GET | `/api/sections/{section}` | Section text (also prefetches its quiz) |
| POST | `/api/explain` | `{section_id, mode: summary\|simple\|paragraph\|concept, paragraph?, concept_id?}` |
| POST | `/api/quiz` | `{section_id}` → 3 questions (answers withheld) |
| POST | `/api/quiz/{id}/answers` | `{answers: {q1: 2, …}}` → feedback, mastery change, gaps, next step |
| GET | `/api/progress/{doc}` | Mastery, gaps with root causes, coach note, history |
| GET | `/api/graph/{doc}` | Nodes with layout (`x`,`y` in 0..1) and edges |
| GET | `/api/graph/{doc}/svg` | Same graph as SVG |
| POST | `/api/reset` | Clear learner progress |

## Layout

| Path | Purpose |
|---|---|
| `src/main.c` | Window, screen stack, toasts, hardware keys, config |
| `src/ui.c` | Flat B/W widget kit: buttons, frames, paged lists, the Pager |
| `src/net.c`, `src/json.c` | Threaded HTTP/1.1 client on GIO, small JSON parser |
| `src/screen_*.c` | Library/contents, reader/explain, quiz/results, progress/map |
| `server/edukindle/agents/` | Socratic Educator, Curriculum Architect, offline fallbacks |
| `server/edukindle/knowledge.py` | Concept graph, BKT, layered layout, SVG |
| `server/edukindle/service.py` | Orchestration, prefetching, persistence |
| `cmake/kindlehf.cmake` | Cross toolchain file |
| `kual/` | KUAL extension metadata, launcher, config example |

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

### 1. Server (your laptop, same Wi-Fi or hotspot as the Kindle)

```sh
cd server
uv sync
export OPENAI_API_KEY=sk-...      # or OPENROUTER_API_KEY=sk-or-...; with neither, heuristic "offline" agents are used
uv run python -m edukindle         # prints the address to use on the Kindle
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
| `EDUKINDLE_AI_LAYOUT` | `1` | `0` turns off AI typesetting of PDF sections |

### 2. Kindle app

Cross-compile (toolchain setup: **[docs/SETUP.md](docs/SETUP.md)**):

```sh
cmake --preset kindlehf && cmake --build --preset kindlehf
cmake --install build/kindlehf --prefix dist --strip
cp -r dist/extensions/edukindle /run/media/$USER/Kindle/extensions/
```

Launch from **KUAL → EduKindle**. The app opens on a connect screen. It tries
the last server that worked. If that fails, it searches the network, and you
can type the address the server printed on a large keypad. The working address
is saved to `extensions/edukindle/edukindle.conf`. Home's **Server** button
brings the screen back. Logs go to `/tmp/edukindle.log`.

### Connecting: networks and firewalls

The Kindle talks to your laptop directly, so both must be on a network that lets
devices reach each other:

- **eduroam and most campus or office Wi-Fi block this** ("client isolation").
  No setting on either device can fix that, so use a **phone hotspot** instead:
  join it on both the laptop and the Kindle, start the server, then tap
  **Find automatically** on the Kindle, or type the address the server printed.
- **The laptop's firewall must allow port 8080.** If ufw is on, the server
  prints the command at startup, for example
  `sudo ufw allow from 192.168.43.0/24 to any port 8080 proto tcp`. Run it
  once per network you use.
- The connect screen explains failures: *refused* means the server isn't
  running on that port, *no reply* means a firewall or an isolating network,
  and *unreachable* means the Kindle is on a different network.

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

**Formatting.** Text from the AI and from uploaded Markdown is a small
Markdown subset with LaTeX maths: paragraphs, `-`/`1.` lists, `**bold**`,
`*italic*`, `` `code` ``, fenced code blocks, `> notes`, `$inline$` and
`$$display$$` maths. The server (`richtext.py`) turns it into blocks for the
Kindle:
- simple inline maths becomes Pango markup: Unicode symbols, real
  super/subscripts and italic variables, all in glyphs the Kindle's Bookerly
  font has. Fractions, sums, limits and roots are typeset as small images that
  sit on the text baseline (Pango shape attributes);
- display maths is rendered by matplotlib (`mathrender.py`) to a PNG at the
  Kindle's text size, and equations too wide for the page break at `=`;
- code keeps its whitespace in the Kindle's monospace font.

**PDFs.** Text extracted from a PDF loses its layout: lines break
mid-sentence, bullets run into paragraphs, and formulas are flattened. When an
AI provider is configured, the typesetter (`agents/typesetter.py`) rewrites
each PDF section as Markdown + LaTeX the first time it's opened, and prepares
the next two in the background. Any result that drops or adds content is
rejected, and the original is kept. Without AI, glued bullets (`•Like this`)
are still split into lists and Unicode maths letters (𝑛, 𝑇) are shown as
italics, since no Kindle font has them.

The Kindle paginates the blocks like a book. Lists get hanging indents, and
headings and formula lead-ins stay with what follows them. Results and progress
pages are composed on the server too (`views.py`), so their wording can change
without redeploying the app.

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
| GET | `/api/health` | `{ok, llm, provider, model}`; the Kindle uses it to find and check servers |
| GET | `/api/sections/{section}` | Section Markdown `text` plus rendered `doc` (also prefetches its quiz) |
| POST | `/api/explain` | `{section_id, mode: summary\|simple\|paragraph\|concept, paragraph?, concept_id?}` → `title`, Markdown `body`, `doc` |
| POST | `/api/quiz` | `{section_id}` → 3 questions with `prompt_markup`/`options_markup` (answers withheld) |
| POST | `/api/quiz/{id}/answers` | `{answers: {q1: 2, …}}` → feedback, mastery change, gaps, next step, results `doc` |
| GET | `/api/progress/{doc}` | Mastery, gaps with root causes, coach note, history, progress `doc` |
| GET | `/api/graph/{doc}` | Nodes with layout (`x`,`y` in 0..1) and edges |
| GET | `/api/graph/{doc}/svg` | Same graph as SVG |
| POST | `/api/reset` | Clear learner progress |

A `doc` is `{"px": n, "blocks": [...]}`. Each block has a type `t` (`p`, `h1`–`h3`,
`li`, `note`, `code`, `math`, `hr`), Pango markup `m`, and the source
`para` index used by *Explain ¶*. The Kindle sends `X-EK-Px` (its text size)
and `X-EK-Width` (page width) so maths images match its screen.

## Layout

| Path | Purpose |
|---|---|
| `src/main.c` | Window, screen stack, toasts, hardware keys, config |
| `src/ui.c` | Flat B/W widget kit: buttons, frames, paged lists, the Pager |
| `src/net.c`, `src/json.c` | Threaded HTTP/1.1 client on GIO, server discovery, small JSON parser |
| `src/screen_*.c` | Connect, library/contents, reader/explain, quiz/results, progress/map |
| `server/edukindle/agents/` | Socratic Educator, Curriculum Architect, offline fallbacks |
| `server/edukindle/richtext.py` | Markdown + LaTeX → Kindle blocks and Pango markup |
| `server/edukindle/mathrender.py` | Display maths → PNG (matplotlib mathtext) |
| `server/edukindle/views.py` | Server-composed results and progress pages |
| `server/edukindle/knowledge.py` | Concept graph, BKT, layered layout, SVG |
| `server/edukindle/service.py` | Orchestration, prefetching, persistence |
| `cmake/kindlehf.cmake` | Cross toolchain file |
| `kual/` | KUAL extension metadata, launcher, config example |

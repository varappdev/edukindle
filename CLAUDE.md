# EduKindle

Distraction-free AI study companion. A C/GTK 2 app runs on jailbroken Kindles
(FW ≥ 5.16.3, `kindlehf`) and talks over plain HTTP to a Python/FastAPI server.
The server runs two OpenAI-backed agents and a knowledge graph. README.md has
the user-facing overview and the API table. docs/SETUP.md covers the cross
toolchain.

## Commands

```sh
# Server (port 8080, data in server/data/, gitignored)
cd server && uv sync
uv run python -m edukindle                    # offline agents if OPENAI_API_KEY unset
OPENAI_API_KEY=sk-... uv run python -m edukindle
uv run pytest                                 # 15 tests, offline + stubbed-LLM paths

# Kindle app, desktop build (needs host GTK 2)
cmake --preset host && cmake --build --preset host
build/host/edukindle --server 127.0.0.1:8080  # Paperwhite-shaped window
build/host/edukindle --server 127.0.0.1:8080 --size 600x800
ctest --test-dir build/host                   # JSON parser + HTTP client tests

# Kindle cross build (toolchain is NOT installed on this dev machine)
cmake --preset kindlehf && cmake --build --preset kindlehf
cmake --install build/kindlehf --prefix dist --strip
```

Dashboard: http://localhost:8080/. Desktop keys: arrows / PgUp / PgDn / Space
turn pages, Esc goes back, Ctrl+Q quits.

Server env vars: `OPENAI_API_KEY`, `EDUKINDLE_MODEL` (default `gpt-5-mini`),
`EDUKINDLE_REASONING` (default `low`, set empty to disable), `OPENAI_BASE_URL`,
`EDUKINDLE_PORT`, `EDUKINDLE_HOST`, `EDUKINDLE_DATA`, `EDUKINDLE_OFFLINE=1`.

Kindle app server address, in priority order: `--server` > `$EDUKINDLE_SERVER`
> `edukindle.conf` (`server=host:port`, in the cwd; `kual/run.sh` creates it
from `edukindle.conf.example`) > `127.0.0.1:8080`.

## Testing the GUI headlessly

Run the app under `Xvfb :77 -screen 0 1072x1448x24`, drive it with
`xdotool mousemove X Y click 1`, and capture with `import -window root`.
Headless `chromium --screenshot` works for the dashboard. Use a scratch
`EDUKINDLE_DATA` dir and a non-default port so the user's data isn't touched.

Never `pkill -f` a pattern that appears in your own command line. It kills the
calling shell. Use `pkill -x edukindle` / `pkill -x Xvfb`, or bracket the
pattern (`pgrep -f "[p]ython -m edukindle"`).

## Architecture

**Server (`server/edukindle/`)**
- `agents/educator.py`: Agent 1, Socratic Educator. Generates quizzes, explains
  text, grades answers, decides readiness (≥2/3 right first time and no tested
  concept weak).
- `agents/curriculum.py`: Agent 2, Curriculum Architect. Maps concepts and
  prerequisites, updates mastery, finds root-cause gaps, picks the next step,
  writes the coach note.
- `agents/offline.py`: heuristic fallbacks, used with no key or when an LLM
  call fails. Every LLM path must keep a fallback.
- `knowledge.py`: NetworkX graph (edge = prerequisite → concept), Bayesian
  Knowledge Tracing, layered layout (x,y in 0..1), SVG.
- `service.py`: orchestration. Background jobs are single-flight per key.
  Quizzes are prefetched when a section opens and after each quiz is served.
- `llm.py`: `LLM.json()` uses strict json_schema and steps down to
  json_object / no reasoning_effort if the model rejects them. Build schemas
  with `obj()`/`arr()` (all fields required, no extra properties). Don't use
  minItems/maxItems; validate after the call instead.
- `store.py`: in-memory store written through to JSON files. Single learner,
  no auth, by design.
- `sample/calculus_derivatives.json`: seeded on first run (`data/.seeded`
  marker). Has hand-written concepts and a `quiz_bank`.

Latency rule: keep OpenAI off the interactive path. Grading is a lookup
because each question stores per-option misconceptions and a hint.

**Kindle app (`src/`)**
- `main.c`: window, screen stack (`app_push` / `app_pop` / `app_replace`,
  `app_on_resume`), toasts, keys, config.
- `ui.c`: flat black-and-white widget kit drawn with Cairo. No gradients:
  never rely on the GTK theme to paint anything. Provides buttons, frames,
  headers, paged lists (`UiList`) and `Pager` (Pango pagination at line
  boundaries).
- `net.c`: HTTP/1.1 client. One worker thread per request, blocking GIO
  sockets, `Connection: close`, result delivered with `g_idle_add`. The
  callback runs only if the owner GObject (the screen) is still alive.
- `json.c`: small DOM parser with NULL-safe getters (`json_str`, `json_int`,
  `json_each`, …).
- `screen_library.c` (home, contents), `screen_reader.c` (reader, explain),
  `screen_quiz.c` (quiz, retry, results), `screen_progress.c` (progress,
  knowledge map).

Screen pattern: `ui_screen()` vbox. State struct attached with
`g_object_set_data_full(screen, "ek-state", st, free_fn)`. Network calls pass
`G_OBJECT(screen)` as the owner. Callbacks may steal `resp->json`
(set it to NULL).

## Conventions & gotchas

- Sizes are design px for a 1072-px-wide Paperwhite. Always wrap them in
  `ui_px()`. Pango markup sizes use `font_desc='%dpx'` with `ui_px(...)`.
- Stay compatible with old Kindle GLib/GTK (GTK 2.24, GLib possibly < 2.32).
  `g_thread_new` is guarded by `GLIB_CHECK_VERSION`. No GTK 3 APIs (e.g.
  `gtk_label_set_lines`) and no `g_autoptr`.
- Avoid rare glyphs. Kindle fonts may lack ✓, ✗, →. `‹ › • · … ¶` and
  `² ³` are safe.
- GTK 2 pitfalls already hit:
  - `gtk_widget_show_all()` does nothing to a widget whose `no_show_all` is
    TRUE. Clear the flag before showing.
  - A GtkEventBox's window excludes `border_width`. Pad with a GtkAlignment
    instead (see `ui_frame`).
  - Wrapped GtkLabels can't ellipsize. Ellipsize single-line list rows instead
    of wrapping them.
- Keep the window title `L:A_N:application_PC:T_ID:com.edukindle.app`. The
  Kindle WM needs it, and `PC:T` keeps the status bar.
- `-Wall -Wextra` must stay warning-free. Run both test suites after changes.

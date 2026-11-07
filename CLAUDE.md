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
uv run python -m edukindle                    # offline agents if no API key is set
OPENAI_API_KEY=sk-... uv run python -m edukindle
OPENROUTER_API_KEY=sk-or-... uv run python -m edukindle   # model: openai/gpt-5-mini
uv run pytest                                 # offline, stubbed-LLM and provider tests

# Kindle app, desktop build (needs host GTK 2)
cmake --preset host && cmake --build --preset host
build/host/edukindle --server 127.0.0.1:8080  # Paperwhite-shaped window
build/host/edukindle --server 127.0.0.1:8080 --size 600x800
ctest --test-dir build/host                   # JSON parser + HTTP client tests

# Kindle cross build (toolchain + SDK installed in ~/x-tools)
cmake --preset kindlehf && cmake --build --preset kindlehf
cmake --install build/kindlehf --prefix dist --strip
# Deploy: Kindle mounts at /run/media/$USER/Kindle (vfat). Replace
# extensions/edukindle with dist/extensions/edukindle and keep or write its
# edukindle.conf, then sync, `udisksctl unmount -b /dev/sdX`, and launch
# from KUAL on the device. Logs: /tmp/edukindle.log (needs SSH).
```

Dashboard: http://localhost:8080/. Desktop keys: arrows / PgUp / PgDn / Space
turn pages, Esc goes back, Ctrl+Q quits. The app starts on the connect screen,
which goes straight to Home when the server answers. A working address is saved
to `edukindle.conf` in the app's cwd (gitignored).

Server env vars: `OPENAI_API_KEY` or `OPENROUTER_API_KEY`, `EDUKINDLE_PROVIDER`
(`openai` / `openrouter`; by default whichever key is set, OpenAI first),
`EDUKINDLE_MODEL` (default `gpt-5-mini`, or `openai/gpt-5-mini` on OpenRouter),
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

- **Use the Kindle's real fonts** so screenshots match the device. Extract them
  from the firmware image the SDK leaves behind:
  `7z e ~/x-tools/kindle-sdk/cache/arm-kindlehf-linux-gnueabihf/firmware_0/rootfs.img usr/java/lib/fonts.sqsh`
  then `7z x fonts.sqsh`. Point a minimal fonts.conf (`<dir>` = that folder)
  at them with `FONTCONFIG_FILE=...`.
- The Bash tool runs **zsh**, which doesn't word-split `$var`. Put
  coordinate-pair helpers in a real `#!/bin/bash` script.
- **Never trigger "Find automatically" on a shared network** such as eduroam:
  it connects to every address in the /24. To test connect and discovery
  failures, run the app in an empty network namespace:
  `unshare -rn sh -c 'ip link set lo up; exec build/host/edukindle ...'`. The X
  socket still works. Discovery itself is unit-tested against 127.0.0.1.

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
- `config.py` / `llm.py`: provider is OpenAI or OpenRouter, both through the
  `openai` SDK. OpenRouter gets its base URL, attribution headers, and
  reasoning effort as `extra_body={"reasoning": {"effort": ...}}` instead of
  `reasoning_effort`. `LLM.json()` uses strict json_schema and steps down to
  json_object / no reasoning effort if the model rejects them. `parse_json`
  tolerates code fences, since some OpenRouter upstreams ignore
  response_format. Build schemas with `obj()`/`arr()` (all fields required,
  no extra properties). Don't use minItems/maxItems; validate after the call
  instead.
- `store.py`: in-memory store written through to JSON files. Single learner,
  no auth, by design.
- `sample/calculus_derivatives.json`: seeded on first run (`data/.seeded`
  marker). Has hand-written concepts and a `quiz_bank`. Its paragraphs are
  Markdown + LaTeX. Bump `"version"` when changing it: stored copies are then
  upgraded in place with the same ids, so progress carries over.
- `richtext.py`: section text, explanations and quiz fields are a Markdown
  subset with `$...$` / `$$...$$` LaTeX. `paragraphs()` defines paragraph
  indices (code and maths chunks stay whole), and every index used by
  *Explain ¶* comes from it, never `split("\n\n")`. `to_blocks()` /
  `doc()` produce Kindle blocks. `inline()` gives Pango markup that is always
  well-formed (fuzz-tested), because Pango draws nothing for bad markup.
  `tex_to_markup()` is inline LaTeX → Unicode + `<sup>`/`<sub>`.
- `mathrender.py`: display maths → grey+alpha PNG via matplotlib mathtext
  (DejaVu Serif, ×1.12 to match Bookerly). Returns None on anything it can't
  parse (`\begin{...}`), and callers fall back to markup. Rendered at the
  Kindle's `X-EK-Px`; equations wider than `X-EK-Width` split at top-level `=`.
- `views.py`: results and progress pages are composed server-side as docs.
- Explanation cache keys include `CACHE_VERSION` (service.py) and the doc
  version. Bump it when explanation formatting changes.

Latency rule: keep OpenAI off the interactive path. Grading is a lookup
because each question stores per-option misconceptions and a hint.

**Kindle app (`src/`)**
- `main.c`: window, screen stack (`app_push` / `app_pop` / `app_replace`,
  `app_on_resume`), toasts, keys, config.
- `ui.c`: flat black-and-white widget kit drawn with Cairo. No gradients:
  never rely on the GTK theme to paint anything. Provides buttons, frames,
  headers, paged lists (`UiList`) and `Pager`. The Pager renders server docs
  (`pager_set_doc`) or plain text: one PangoLayout per block, lines become
  rows, and pages break between rows. Headings, colon lead-ins and rows of a
  split equation are kept with what follows. Maths PNGs are drawn with
  `cairo_mask_surface`.
- `net.c`: HTTP/1.1 client. One worker thread per request, blocking GIO
  sockets, `Connection: close`, result delivered with `g_idle_add`. The
  callback runs only if the owner GObject (the screen) is still alive. Each
  request copies the server address, so `net_init()` can change it at
  runtime. `net_discover()` connects to every address on the device's subnets
  at once (non-blocking connect + poll, capped at a /24 each), then checks
  `/api/health`.
- `screen_connect.c`: startup / Server screen (saved address → discovery →
  keypad), with failure advice based on the socket error.
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
- The Kindle firmware (5.16+/5.18) really has **GTK 2.20, GLib 2.29,
  Pango 1.26 and cairo 1.10** (check the `.so` version suffixes in the
  sysroot). kindle-sdk's `.pc` files wrongly claim GTK 2.24 / GLib 2.82, so
  trust the libraries, not pkg-config; the cross build's linker is the real
  API check. Pango 1.26 does support `font_desc='NNpx'`, `<sup>`/`<sub>` and
  `font_family`. cairo has PNG support built in. Nothing newer than GLib 2.29
  is available. The `g_thread_new` / `g_thread_init` paths are guarded, and
  CMake links `libgthread` directly because there's no `gthread-2.0.pc`. No
  GTK 3 APIs (e.g. `gtk_label_set_lines`) and no `g_autoptr`. The host build
  compiles the modern-GLib branch, so only the cross build checks the old one.
- Kindle fonts: Bookerly (reading), Amazon Ember (UI) and **KindleBlackboxC**
  (monospace) are on the device. Bookerly and Ember cover Greek, math
  operators, arrows, super/subscript digits and combining accents, and Code2000
  fills gaps. Missing: ✗ ⊂ ↦ ∮ and the combining arrow, so render `\vec` as
  bold. Name the monospace font `KindleBlackboxC Regular`: all four of its
  files claim the same weight, and the bare family resolves to Bold.
- GTK 2 pitfalls already hit:
  - `gtk_widget_show_all()` does nothing to a widget whose `no_show_all` is
    TRUE. Clear the flag before showing.
  - A GtkEventBox's window excludes `border_width`. Pad with a GtkAlignment
    instead (see `ui_frame`).
  - Wrapped GtkLabels can't ellipsize. Ellipsize single-line list rows instead
    of wrapping them.
  - Labels don't clip: text longer than a label's size request overlaps
    whatever is below. Keep fixed-height status text short.
- Don't name functions after libc ones (`on_exit` clashes with glibc).
- Keep the window title `L:A_N:application_PC:T_ID:com.edukindle.app`. The
  Kindle WM needs it, and `PC:T` keeps the status bar.
- `-Wall -Wextra` must stay warning-free. Run both test suites after changes.

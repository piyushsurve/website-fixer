# Website Fixer

A timed HTML/CSS **repair** game. Round 01 hands the player a finished
cloud-platform landing page ("NovaCloud") that shipped broken, and gives them
**30 minutes** to clear **14 objectives** by fixing the existing HTML and CSS
in the browser.

Nobody builds NovaCloud. The markup, the copy, the sections and the design
system are all already there; ten CSS declarations and four bits of markup are
wrong, and every one of them is visible in the live preview.

Django 5 + Django Channels (ASGI). No frontend framework.

---

## Run it locally

```bash
cd website-fixer
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. `daphne` is first in `INSTALLED_APPS`, so
`runserver` serves ASGI and the WebSocket works without any extra process.

To serve exactly the way production does:

```bash
python manage.py collectstatic --no-input
daphne -b 127.0.0.1 -p 8000 games.asgi:application
```

---

## How it fits together

| Area | Where |
| --- | --- |
| Challenge files (the site itself) | `first/challenge/novacloud/` |
| Round metadata + duration + fix table | `first/game_config.py` |
| Objective checking | `first/checks.py` |
| Views / API | `first/views.py`, `games/urls.py` |
| Presence store | `first/presence.py` |
| WebSocket consumer | `first/consumers.py`, `first/routing.py`, `games/asgi.py` |
| Game shell UI | `static/css/wf-*.css`, `static/js/wf-*.js`, `template/*.html` |

Flow: `/` (home) → `/signup/` or `/login/` → `/start/` (launch screen) →
`/home/` (arena) → success or timeout.

### The challenge is four files

```
first/challenge/novacloud/
  index.html      the broken page handed to the player
  style.css       the broken stylesheet handed to the player
  solution.html   the page with every objective fixed
  solution.css    the organisers' gold standard
```

`index.html` is a **complete HTML document**, not a fragment. The preview
renders it as written and swaps its `<link rel="stylesheet">` for the player's
live CSS (`previewDocument()` in `static/js/wf-arena.js`).

`solution.*` is never served to a browser — `ArenaPageTests` asserts that the
answers do not appear in the arena HTML.

### 38 defects, 14 objectives

`style.css` ships with **38 deliberate defects**; only **14 are graded**. The
rest are visual noise: squashed pricing cards, a four-column footer, cropped
avatars. A player may fix them or ignore them, and the arena says so plainly.

`GRADED_CSS_FIXES` / `GRADED_HTML_FIXES` in `first/game_config.py` are the
authoritative list of what is scored — objective id to the `(broken, fixed)`
edits that clear it. Grouped edits count as one objective: the feature card
needs both its padding and its radius, the stats band needs all four numbers.
`apply_fixes()` raises if an anchor is missing or ambiguous, so a drifted
challenge file fails the suite loudly rather than grading the wrong thing.

One defect from the organisers' `style.css` is deliberately **not** shipped:
`.hero__glow { position: static }` puts a 900×900 decorative div into normal
flow, opening a 900px void that pushes the hero headline from 715px to 1615px.
Four of the fourteen objectives live in that hero, so the defect hides the
round rather than decorating it.

To swap in a different site: replace the four files, update the two fix tables,
and point the checks in `first/checks.py` at the new selectors. Nothing in the
views, templates or JS knows what the challenge is.

### The objectives

14 objectives: **10 CSS, 4 HTML**. They are graded on *outcome*, not on text —
whitespace, property order, comments and equivalent answers (`flex` vs
`inline-flex`, `56px` vs `clamp(...)`, `repeat(3, 1fr)` vs `auto-fit`, a
literal `16px` vs the `--radius-md` token) all pass. Each objective carries a
description plus **three hints** that the player unlocks one at a time: what
looks wrong, which concept is involved, and finally which property in which
rule.

Two objectives interact, on purpose. The reversed breakpoint
(`@media (min-width: 860px)`) applies the entire phone stylesheet to desktop,
which overrides the hero's column count — so fixing `css-hero-split` shows no
visual change until `css-responsive` is fixed too. Both are graded
independently and the hint for the first one says so.

### JavaScript

The page ships without any. The supplied markup references a `script.js` that
would have driven the theme toggle, the FAQ accordion, the mobile menu and a
stat counter; the preview sandbox blocks scripts, so that tag is removed and
the four headline numbers live in the markup instead of only in their
`data-count-to` attributes — which is the `html-stats` objective. The
stylesheet's `.reveal` rules are dead code: the markup never uses that class.

### Why the preview is scaled

`fitPreview()` in `static/js/wf-arena.js` renders the iframe at a fixed 1120px
virtual width and scales it down to the panel. Sizing the iframe to the panel
instead would put NovaCloud permanently inside its own 860px breakpoint and
hide the desktop-only mistakes. The **Phone** toggle switches to a 390px
virtual width.

### The 30 minute timer

`GAME_DURATION_SECONDS = 30 * 60` lives in `first/game_config.py`.

`User.game_start_time` is stamped **once**, the first time the player opens the
arena (`User.start_challenge()` is a no-op if it is already set). Every
remaining-time value is computed on the server from that timestamp:

* the arena page ships the current `remaining` in `#wf-arena-data`;
* the browser only *renders* a countdown from it and re-syncs with
  `GET /api/state/` every 20 seconds and on window focus;
* `POST /save-css/`, `POST /api/check/` and `POST /api/reset/` all re-check
  `user.is_locked` and refuse the write when the session is over.

Refreshing, editing `timeLeft` in devtools, or clearing local storage changes
nothing — the clock is a database timestamp.

### CSS isolation

The player's page is rendered **only** inside `<iframe id="wf-preview" sandbox>`
via `srcdoc` (`static/js/wf-arena.js`). An empty `sandbox` attribute means no
scripts, no forms, no same-origin access and no top-level navigation, so:

* player CSS applies to that document alone and can never reach the game shell;
* game-shell CSS (all `wf-` prefixed) never reaches the player's page, because
  it is a different document;
* player JavaScript does not run at all — the challenge is HTML + CSS;
* the preview cannot read cookies, the session, or the parent DOM.

Server side, player HTML/CSS is never rendered with `|safe`; it only appears
inside auto-escaped `<textarea>` elements and is parsed (never executed) by
`first/checks.py`.

### Live player count

`/ws/presence/` (`first/consumers.py`) is a Channels consumer joined to one
broadcast group. Presence is keyed by **Django session**, so one browser with
five tabs counts as one player. Each client pings every 20 seconds; entries
older than 55 seconds are swept, which covers dropped sockets, instance
restarts and closed laptops. The count is broadcast on every connect and
disconnect, and the browser reconnects with exponential backoff.

The client picks its scheme from the page (`https:` → `wss://`, otherwise
`ws://`) and uses `location.host`, so nothing about the environment is
hardcoded.

### Do I need Redis?

**No, not for a single instance** — which is what Render's free/starter web
service is. The default `InMemoryChannelLayer` plus the in-process presence
store are exact when one process serves everybody.

Set `REDIS_URL` **only if you scale to more than one instance**. Doing so
switches the channel layer to `channels_redis` *and* the presence store to a
Redis sorted set, so the count is shared. Nothing else changes.

---

## Deploying to Render

* **Build Command:** `./build.sh`
  (installs requirements, `collectstatic`, `migrate`)
* **Start Command:** `daphne -b 0.0.0.0 -p $PORT games.asgi:application`
* **Environment variables:**

  | Key | Value |
  | --- | --- |
  | `PYTHON_VERSION` | `3.11.9` |
  | `DJANGO_SECRET_KEY` | generate a value |
  | `DJANGO_DEBUG` | `false` |
  | `DATABASE_URL` | Postgres connection string (optional; SQLite otherwise) |
  | `REDIS_URL` | only when running 2+ instances |
  | `DJANGO_SSL_REDIRECT` | defaults to `true` when `DJANGO_DEBUG=false`; set `false` only to run a production-shaped server locally over plain HTTP |

`render.yaml` in this folder describes the same thing as a blueprint.

Gunicorn was removed from `requirements.txt`: it is WSGI-only and would kill
the WebSocket. Daphne serves both HTTP and WS.

---

## Running an event

Players register themselves at `/signup/` with their PC number. Accounts
created before this version already carry a `game_start_time`, so their clock
looks expired — clear that field in `/admin/` (or set `completed_at` back to
empty) to hand a player a fresh 30 minutes.

`python manage.py test` runs the regression suite (36 tests). It asserts that
the shipped page fails all 14 objectives, that both the gold standard *and* a
graded-fixes-only submission pass all 14, that each individual fix clears
exactly one objective and no others, that equivalent beginner answers are
accepted, that the FAQ icon's legitimate `rotate(45deg)` is never mistaken for
the console's, that the answers never reach the browser, that the clock cannot
be restarted or beaten, and that the presence socket counts one browser session
as one player however many tabs it opens.

## Testing the player count by hand

1. Start the server and open <http://127.0.0.1:8000/> — the badge reads
   `1 player online`.
2. Open a second **private/incognito** window on the same URL — both windows
   move to `2 players online` without a refresh.
3. Open a second tab in the *same* window — the count stays at 2 (same session).
4. Close the private window — the first window drops back to `1`.
5. Refresh either window — the count settles back to the same number; it does
   not climb.

Two normal windows of the same browser share a session cookie and therefore
count as one player. That is intentional; use a private window to simulate a
second person.

## Testing CSS isolation by hand

Paste this into the `style.css` tab in the arena:

```css
* { margin: 0 !important; padding: 0 !important; }
body { background: red !important; }
button { background: lime !important; font-size: 50px !important; }
header { display: none !important; }
```

The preview turns red and loses its header. The game bar, timer, buttons and
editor must not change at all.

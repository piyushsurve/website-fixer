"""
Single source of truth for the Website Fixer challenge.

Everything the game needs to know about "what the player is fixing" and
"how long they get" lives here, so views, checks and templates never
hardcode duplicate copies of it.

The challenge itself is *not* written in this file. Round 01 ships as four
plain files under ``first/challenge/novacloud/``:

    index.html      the broken page handed to the player
    style.css       the broken stylesheet handed to the player
    solution.html   the page with every objective fixed
    solution.css    the organisers' gold standard

`style.css` ships 39 deliberate defects; 14 of them are graded objectives.
The rest are visual noise the player may fix or ignore -- `GRADED_FIXES`
below is the authoritative list of what is scored, and `checks.py` grades
the *outcome* of each one rather than matching this text.
"""

from pathlib import Path

# Hard 30 minute session.
GAME_DURATION_SECONDS = 30 * 60

# Below this many seconds the UI switches the timer into its "danger" state.
TIMER_DANGER_SECONDS = 5 * 60
TIMER_WARNING_SECONDS = 10 * 60

# How often the browser re-syncs its countdown with the server.
TIMER_SYNC_INTERVAL_SECONDS = 20


# ---------------------------------------------------------------- round ----

ROUND_NUMBER = '01'
SITE_NAME = 'NovaCloud'
SITE_TAGLINE = 'AI-powered cloud platform landing page'
DIFFICULTY = 'Basic'

CHALLENGE_DIR = Path(__file__).resolve().parent / 'challenge' / 'novacloud'


def _read(name):
    return (CHALLENGE_DIR / name).read_text(encoding='utf-8')


# The broken page the player has to repair. Unlike earlier rounds this is a
# complete HTML document -- the preview renders it as-is, swapping the
# stylesheet link for the player's live CSS.
STARTER_HTML = _read('index.html')
STARTER_CSS = _read('style.css')

# The intended result. Used by the tests (and by organisers) to prove the
# challenge is solvable; never sent to the browser.
SOLUTION_HTML = _read('solution.html')
SOLUTION_CSS = _read('solution.css')


# ----------------------------------------------------------------- fixes ----
#
# objective id -> the (broken, fixed) edits that clear it. Grouped edits
# count as one objective: repairing the feature card means fixing both its
# padding and its corner radius, and the stats band needs all four numbers.
#
# These are the *reference* answers. The checker accepts any equivalent
# result, so this table is for the tests and for organisers -- not a
# string comparison the player has to match.

GRADED_CSS_FIXES = {
    'css-line-height': (
        ('  line-height: 1;\n  -webkit-font-smoothing',
         '  line-height: 1.6;\n  -webkit-font-smoothing'),
    ),
    'css-navbar-row': (
        ('.navbar {\n  display: block;', '.navbar {\n  display: flex;'),
    ),
    'css-nav-spacing': (
        ('  gap: 2px;\n  flex: 1;\n  justify-content: flex-end;\n}',
         '  gap: 32px;\n  flex: 1;\n  justify-content: center;\n}'),
    ),
    'css-hero-split': (
        ('  display: grid;\n  grid-template-columns: 1fr;\n  align-items: center;\n  gap: 64px;',
         '  display: grid;\n  grid-template-columns: 1fr 1fr;\n  align-items: center;\n  gap: 64px;'),
    ),
    'css-hero-title': (
        ('.hero__title {\n  font-size: 1rem;',
         '.hero__title {\n  font-size: clamp(2.4rem, 4.4vw, 3.6rem);'),
    ),
    'css-hero-gap': (
        ('.hero__actions {\n  display: flex;\n  align-items: center;\n  gap: 150px;',
         '.hero__actions {\n  display: flex;\n  align-items: center;\n  gap: 16px;'),
    ),
    'css-console': (
        ('  overflow: hidden;\n  transform: rotate(45deg);',
         '  overflow: hidden;\n  transform: rotate(1.2deg);'),
    ),
    'css-features': (
        ('.features__grid {\n  display: grid;\n  grid-template-columns: repeat(1, 1fr);',
         '.features__grid {\n  display: grid;\n  grid-template-columns: repeat(3, 1fr);'),
    ),
    'css-feature-box': (
        ('  border-radius: 0;\n  padding: 4px;\n  transition: transform',
         '  border-radius: var(--radius-md);\n  padding: 32px;\n  transition: transform'),
    ),
    'css-responsive': (
        ('@media (min-width: 860px) {', '@media (max-width: 860px) {'),
    ),
}

GRADED_HTML_FIXES = {
    'html-h1': (
        ('          <h2 class="hero__title">\n'
         '            Ship infrastructure at the\n'
         '            <span class="hero__title-accent">speed of thought</span>\n'
         '          </h2>',
         '          <h1 class="hero__title">\n'
         '            Ship infrastructure at the\n'
         '            <span class="hero__title-accent">speed of thought</span>\n'
         '          </h1>'),
    ),
    'html-nav-link': (
        ('        <li><a href="#testimonials" class="navbar__link">Testimonials</a></li>\n      </ul>',
         '        <li><a href="#testimonials" class="navbar__link">Testimonials</a></li>\n'
         '        <li><a href="#faq" class="navbar__link">FAQ</a></li>\n      </ul>'),
    ),
    'html-feature-card': (
        ('          <article>\n'
         '            <div class="feature-card__icon" aria-hidden="true">\n'
         '              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">\n'
         '                <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>',
         '          <article class="feature-card">\n'
         '            <div class="feature-card__icon" aria-hidden="true">\n'
         '              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">\n'
         '                <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'),
    ),
    'html-stats': (
        ('data-count-to="12000">0<', 'data-count-to="12000">12,000<'),
        ('data-count-to="99">0<', 'data-count-to="99">99<'),
        ('data-count-to="14">0<', 'data-count-to="14">14<'),
        ('data-count-to="6">0<', 'data-count-to="6">6<'),
    ),
}

GRADED_FIXES = {**GRADED_CSS_FIXES, **GRADED_HTML_FIXES}


def apply_fixes(source, fixes):
    """Apply every (broken, fixed) pair to `source`, exactly once each.

    `fixes` may be a flat sequence of pairs or a mapping of objective id ->
    pairs. Raises if an anchor is missing or ambiguous, so a drifted
    challenge file fails loudly instead of silently grading the wrong thing.
    """
    if isinstance(fixes, dict):
        fixes = [pair for pairs in fixes.values() for pair in pairs]

    for broken, fixed in fixes:
        if source.count(broken) != 1:
            raise ValueError(f'fix anchor is not unique: {broken!r}')
        source = source.replace(broken, fixed, 1)
    return source

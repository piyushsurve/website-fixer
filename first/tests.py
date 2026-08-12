"""
Regression tests for the challenge itself.

Round 01 is CSS only. The markup is fixed, read-only and never accepted from
the browser, so the properties that matter are: the shipped stylesheet fails
every objective, the gold standard passes every one, each graded fix clears
exactly its own box, equivalent answers are accepted, a posted `html` field
is ignored, and the clock cannot be restarted.

`style.css` carries 37 deliberate defects while only 14 are graded, so
"apply the graded fixes" produces a *passing* submission, not a pristine one.
`solution.css` remains the organisers' reference.
"""

import re
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from games.asgi import application as presence_application

from . import presence as first_presence
from .checks import CSS_CHECKS, HTML_CHECKS, TOTAL_CHECKS, run_checks
from .game_config import (
    CHALLENGE_HTML,
    GAME_DURATION_SECONDS,
    GRADED_FIXES,
    SOLUTION_CSS,
    STARTER_CSS,
    apply_fixes,
)
from .models import User
from .presence import HEARTBEAT_INTERVAL_SECONDS, get_presence_store


def graded_solution():
    """The minimum passing stylesheet: only the 14 graded objectives fixed."""
    return apply_fixes(STARTER_CSS, GRADED_FIXES)


def results_by_id(css, html=None):
    return {r['id']: r['passed'] for r in run_checks(html or CHALLENGE_HTML, css)}


class ChallengeSourceTests(TestCase):
    """The challenge files must stay consistent with the fix table."""

    def test_every_graded_fix_anchors_uniquely(self):
        # apply_fixes raises when an anchor is missing or ambiguous, so this
        # fails loudly the moment a challenge file drifts from the table.
        apply_fixes(STARTER_CSS, GRADED_FIXES)

    def test_the_round_is_css_only(self):
        self.assertEqual(HTML_CHECKS, 0)
        self.assertEqual(CSS_CHECKS, TOTAL_CHECKS)
        self.assertEqual(len(GRADED_FIXES), TOTAL_CHECKS)
        self.assertEqual({d['id'] for d in run_checks('', '')}, set(GRADED_FIXES))

    def test_the_round_stays_beginner_sized(self):
        # 12-16 objectives. A wider brief would stop being a 30 minute
        # beginner round, so lock the shape in.
        self.assertTrue(12 <= TOTAL_CHECKS <= 16, TOTAL_CHECKS)

    def test_every_objective_ships_three_hints(self):
        for check in run_checks(CHALLENGE_HTML, STARTER_CSS):
            self.assertEqual(len(check['hints']), 3, check['id'])
            self.assertTrue(check['description'].strip(), check['id'])

    def test_the_markup_is_the_finished_novacloud_page(self):
        # The player repairs a stylesheet; the page itself is already correct.
        self.assertIn('<!DOCTYPE html>', CHALLENGE_HTML)
        self.assertEqual(CHALLENGE_HTML.count('<h1'), 1)
        for section in ('id="features"', 'id="pricing"', 'id="faq"',
                        'id="testimonials"', 'id="how-it-works"', 'site-footer'):
            self.assertIn(section, CHALLENGE_HTML, section)
        # the five nav links all resolve to real sections
        self.assertEqual(CHALLENGE_HTML.count('class="navbar__link"'), 5)
        self.assertGreater(len(CHALLENGE_HTML), 20000)

    def test_the_page_needs_no_javascript(self):
        # The preview sandbox blocks scripts, so nothing may depend on them.
        self.assertNotIn('<script', CHALLENGE_HTML)
        self.assertNotIn('class="reveal"', CHALLENGE_HTML)
        # the statistics read their real values rather than a JS placeholder
        for number in ('>12,000<', '>99<', '>14<', '>6<'):
            self.assertIn(number, CHALLENGE_HTML, number)
        # the FAQ answers are readable without an accordion script
        self.assertNotIn('.faq-item__answer {\n  max-height: 0;', SOLUTION_CSS)
        self.assertNotIn('.faq-item__answer {\n  max-height: 0;', STARTER_CSS)
        # ...and the theme toggle shows one icon, not both
        self.assertIn('.theme-toggle__icon--moon {\n  display: none;\n}', STARTER_CSS)

    def test_the_stylesheet_carries_more_noise_than_objectives(self):
        # A deliberate design decision: 37 defects ship, 14 are scored.
        differing = sum(1 for a, b in zip(SOLUTION_CSS.splitlines(),
                                          STARTER_CSS.splitlines()) if a != b)
        self.assertGreater(differing, TOTAL_CHECKS)

    def test_the_hero_glow_stays_out_of_normal_flow(self):
        # `position: static` on the 900x900 glow opens a 900px void above the
        # hero and hides four objectives. It must never come back.
        self.assertIn('.hero__glow {\n  position: absolute;', STARTER_CSS)


class ChallengeCheckTests(TestCase):
    def test_shipped_stylesheet_fails_every_objective(self):
        results = run_checks(CHALLENGE_HTML, STARTER_CSS)
        self.assertEqual(len(results), TOTAL_CHECKS)
        self.assertEqual([r['id'] for r in results if r['passed']], [])

    def test_gold_standard_passes_every_objective(self):
        failed = [r['id'] for r in run_checks(CHALLENGE_HTML, SOLUTION_CSS) if not r['passed']]
        self.assertEqual(failed, [])

    def test_fixing_only_the_graded_objectives_is_enough_to_win(self):
        css = graded_solution()
        failed = [r['id'] for r in run_checks(CHALLENGE_HTML, css) if not r['passed']]
        self.assertEqual(failed, [])
        self.assertNotEqual(css, SOLUTION_CSS)  # deliberately not pristine

    def test_each_fix_clears_exactly_its_own_objective(self):
        """Fixing one thing must not accidentally tick a different box."""
        for objective, pairs in GRADED_FIXES.items():
            css = apply_fixes(STARTER_CSS, pairs)
            passed = [k for k, v in results_by_id(css).items() if v]
            self.assertEqual(passed, [objective])

    def test_grouped_objectives_need_all_their_parts(self):
        """A half-finished grouped fix must not score."""
        for objective in ('css-stats-band', 'css-steps', 'css-pricing'):
            pairs = GRADED_FIXES[objective]
            self.assertEqual(len(pairs), 2, objective)
            for half in pairs:
                css = apply_fixes(STARTER_CSS, (half,))
                self.assertFalse(results_by_id(css)[objective], f'{objective} half {half[0][:30]}')

    def test_alternative_but_valid_answers_are_accepted(self):
        css = graded_solution()

        auto_fit = css.replace('.features__grid {\n  display: grid;\n'
                               '  grid-template-columns: repeat(3, 1fr);',
                               '.features__grid {\n  display: grid;\n'
                               '  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));')
        self.assertTrue(results_by_id(auto_fit)['css-features'])

        three_tracks = css.replace('.features__grid {\n  display: grid;\n'
                                   '  grid-template-columns: repeat(3, 1fr);',
                                   '.features__grid {\n  display: grid;\n'
                                   '  grid-template-columns: 1fr 1fr 1fr;')
        self.assertTrue(results_by_id(three_tracks)['css-features'])

        # a plain size instead of the design system's clamp()
        plain = css.replace('font-size: clamp(2.4rem, 4.4vw, 3.6rem);', 'font-size: 56px;')
        self.assertTrue(results_by_id(plain)['css-hero-title'])

        # inline-flex is as good as flex for the header bar
        inline = css.replace('.navbar {\n  display: flex;', '.navbar {\n  display: inline-flex;')
        self.assertTrue(results_by_id(inline)['css-navbar-row'])

        # rem units, and a literal radius instead of the token
        rem_gap = css.replace('  gap: 32px;\n  flex: 1;', '  gap: 2rem;\n  flex: 1;')
        self.assertTrue(results_by_id(rem_gap)['css-nav-spacing'])

        literal = css.replace('  border-radius: var(--radius-md);\n  padding: 32px;',
                              '  border-radius: 16px;\n  padding: 2rem;')
        self.assertTrue(results_by_id(literal)['css-feature-box'])

        # deleting the tilt is as good as reducing it
        for value in ('rotate(0deg)', 'none'):
            upright = STARTER_CSS.replace('  overflow: hidden;\n  transform: rotate(45deg);',
                                          '  overflow: hidden;\n  transform: ' + value + ';')
            self.assertTrue(results_by_id(upright)['css-console'], value)

        # dropping the featured card's transform entirely is a valid answer
        no_scale = css.replace('  border-color: transparent;\n  transform: scale(1.04);',
                               '  border-color: transparent;')
        self.assertTrue(results_by_id(no_scale)['css-pricing'])

        # a square icon at any size, not just 48px
        bigger_icon = css.replace('  width: 48px;\n  height: 48px;\n'
                                  '  border-radius: var(--radius-sm);',
                                  '  width: 56px;\n  height: 56px;\n'
                                  '  border-radius: var(--radius-sm);')
        self.assertTrue(results_by_id(bigger_icon)['css-feature-icon'])

    def test_formatting_noise_does_not_affect_grading(self):
        css = graded_solution().replace(
            '.navbar {\n  display: flex;\n  align-items: center;',
            '.navbar{align-items:center;display:flex;  /* tidied */')
        self.assertTrue(results_by_id(css)['css-navbar-row'])

    def test_the_faq_icon_rotation_is_not_mistaken_for_the_console(self):
        # `.faq-item--open .faq-item__icon` legitimately uses rotate(45deg).
        self.assertIn('.faq-item--open .faq-item__icon', STARTER_CSS)
        self.assertTrue(results_by_id(graded_solution())['css-console'])
        self.assertFalse(results_by_id(STARTER_CSS)['css-console'])

    def test_hover_rules_do_not_satisfy_base_selectors(self):
        css = STARTER_CSS.replace('.navbar__link:hover {\n  color: var(--color-text);',
                                  '.navbar__link:hover {\n  color: var(--color-text);\n'
                                  '  display: flex;')
        self.assertFalse(results_by_id(css)['css-navbar-row'])

    def test_media_query_answers_are_scoped_to_the_media_query(self):
        css = graded_solution()
        stripped = css.replace('@media (max-width: 860px) {', '@media (min-width: 861px) {')
        self.assertFalse(results_by_id(stripped)['css-responsive'])
        self.assertTrue(results_by_id(stripped)['css-hero-split'])

    def test_malformed_submission_does_not_raise(self):
        results = run_checks(CHALLENGE_HTML, 'body { color: ; } @media { .x {')
        self.assertEqual(len(results), TOTAL_CHECKS)

    def test_hostile_css_is_graded_without_crashing(self):
        hostile = '* { all: unset !important; } body { background: red !important; }'
        results = results_by_id(hostile)
        self.assertEqual(len(results), TOTAL_CHECKS)
        # Deleting the stylesheet does not solve the round. (`css-console`
        # legitimately passes: with no transform declared, nothing is rotated.)
        for objective in ('css-line-height', 'css-navbar-row', 'css-nav-spacing',
                          'css-hero-split', 'css-hero-title', 'css-hero-gap',
                          'css-stats-band', 'css-features', 'css-feature-box',
                          'css-feature-icon', 'css-steps', 'css-pricing',
                          'css-responsive'):
            self.assertFalse(results[objective], objective)


class ReadOnlyMarkupTests(TestCase):
    """The markup is fixed: it is never taken from the client, ever."""

    def setUp(self):
        self.user = User.objects.create_user(username='Tester', pc_no='PC-RO', password='pw-123456')
        self.client.force_login(self.user, backend='first.backends.PCNoBackend')
        self.client.get(reverse('home'))

    def test_a_posted_html_field_is_ignored_on_save(self):
        self.client.post(reverse('save_css'), {'html': '<h1>hacked</h1>', 'css': STARTER_CSS})
        served = self.client.get(reverse('get_css')).json()
        self.assertEqual(served['html'], CHALLENGE_HTML)
        self.assertNotIn('hacked', served['html'])

    def test_a_posted_html_field_cannot_change_the_score(self):
        # Every objective is CSS, so even a "perfect" forged page scores zero.
        data = self.client.post(reverse('api_check'),
                                {'html': CHALLENGE_HTML, 'css': STARTER_CSS}).json()
        self.assertEqual(data['passed'], 0)

        data = self.client.post(reverse('api_check'),
                                {'html': '<h1>hacked</h1>', 'css': graded_solution()}).json()
        self.assertEqual(data['passed'], TOTAL_CHECKS)

    def test_reset_returns_the_stylesheet_only(self):
        self.client.post(reverse('save_css'), {'css': 'body{}'})
        data = self.client.post(reverse('api_reset'), {}).json()
        self.assertEqual(data['css'], STARTER_CSS)
        self.assertNotIn('html', data)

    def test_the_arena_marks_the_html_pane_read_only(self):
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn('id="wf-html"', page)
        self.assertIn('readonly', page)
        self.assertIn('aria-readonly="true"', page)
        self.assertIn('read only', page)


class ChallengeClockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='Tester', pc_no='PC-TEST', password='pw-123456')
        self.client.force_login(self.user, backend='first.backends.PCNoBackend')

    def test_clock_starts_on_first_arena_visit_and_never_restarts(self):
        self.assertIsNone(self.user.game_start_time)

        self.client.get(reverse('home'))
        self.user.refresh_from_db()
        started = self.user.game_start_time
        self.assertIsNotNone(started)

        self.client.get(reverse('home'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.game_start_time, started)

    def test_state_endpoint_reports_the_full_duration_at_the_start(self):
        self.client.get(reverse('home'))
        data = self.client.get(reverse('api_state')).json()
        self.assertEqual(data['duration'], GAME_DURATION_SECONDS)
        self.assertGreater(data['remaining'], GAME_DURATION_SECONDS - 10)
        self.assertFalse(data['expired'])

    def test_reset_restores_the_broken_css_without_touching_the_clock(self):
        self.client.get(reverse('home'))
        self.client.post(reverse('save_css'), {'css': graded_solution()})

        self.user.refresh_from_db()
        started = self.user.game_start_time

        data = self.client.post(reverse('api_reset'), {}).json()
        self.assertEqual(data['css'], STARTER_CSS)

        self.user.refresh_from_db()
        self.assertEqual(self.user.game_start_time, started)

    def test_expired_session_refuses_saves_and_checks(self):
        self.client.get(reverse('home'))
        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])

        saved = self.client.post(reverse('save_css'), {'css': 'body{}'}).json()
        self.assertEqual(saved['error'], 'Time is up!')

        checked = self.client.post(reverse('api_check'), {'css': graded_solution()}).json()
        self.assertEqual(checked['error'], 'Time is up!')
        self.assertFalse(checked['completed'])

        self.user.refresh_from_db()
        self.assertIsNone(self.user.completed_at)
        self.assertNotEqual(self.client.get(reverse('get_css')).json()['css'], 'body{}')

    def test_expired_session_also_refuses_reset(self):
        self.client.get(reverse('home'))
        self.client.post(reverse('save_css'), {'css': 'body{ /* mine */ }'})
        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])

        data = self.client.post(reverse('api_reset'), {}).json()
        self.assertNotIn('css', data)
        self.assertEqual(self.client.get(reverse('get_css')).json()['css'], 'body{ /* mine */ }')

    def test_solving_the_challenge_completes_and_locks_the_session(self):
        self.client.get(reverse('home'))
        data = self.client.post(reverse('api_check'), {'css': graded_solution()}).json()

        self.assertEqual(data['passed'], TOTAL_CHECKS)
        self.assertTrue(data['completed'])
        self.assertTrue(data['locked'])

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.completed_at)
        self.assertEqual(self.user.best_score, TOTAL_CHECKS)

    def test_partial_progress_is_recorded_as_a_best_score(self):
        self.client.get(reverse('home'))
        some = dict(list(GRADED_FIXES.items())[:3])
        data = self.client.post(reverse('api_check'), {'css': apply_fixes(STARTER_CSS, some)}).json()

        self.assertEqual(data['passed'], 3)
        self.assertFalse(data['completed'])

        self.client.post(reverse('api_check'), {'css': STARTER_CSS})
        self.user.refresh_from_db()
        self.assertEqual(self.user.best_score, 3)

    def test_autosaved_work_counts_even_without_running_the_checks(self):
        self.client.get(reverse('home'))
        some = dict(list(GRADED_FIXES.items())[:4])
        self.client.post(reverse('save_css'), {'css': apply_fixes(STARTER_CSS, some)})

        self.client.get(reverse('home'))  # grading happens on render
        self.user.refresh_from_db()
        self.assertEqual(self.user.best_score, 4)

    def test_running_out_of_time_closes_the_round_even_on_a_perfect_page(self):
        self.client.get(reverse('home'))
        self.client.post(reverse('save_css'), {'css': graded_solution()})

        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])

        self.client.get(reverse('home'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.best_score, TOTAL_CHECKS)
        self.assertIsNone(self.user.completed_at, 'time ran out before they submitted')
        self.assertTrue(self.user.is_expired)

    def test_anonymous_visitors_cannot_touch_the_api(self):
        self.client.logout()
        for name in ('save_css', 'api_check', 'api_reset'):
            self.assertEqual(self.client.post(reverse(name), {}).status_code, 403, name)
        self.assertEqual(self.client.get(reverse('api_state')).status_code, 403)


class ArenaPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='Tester', pc_no='PC-UI', password='pw-123456')
        self.client.force_login(self.user, backend='first.backends.PCNoBackend')

    def test_home_page_advertises_the_round(self):
        page = self.client.get(reverse('intro')).content.decode()
        self.assertIn('NovaCloud', page)
        self.assertIn('Repair the code. Beat the clock.', page)
        self.assertIn('Round 01', page)
        self.assertIn(str(TOTAL_CHECKS), page)
        self.assertIn('CSS DEBUGGING', page)
        self.assertIn('read-only', page)

    def test_arena_renders_the_broken_css_and_every_objective(self):
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn('wf-preview', page)
        self.assertIn('sandbox', page)
        for check in run_checks(CHALLENGE_HTML, STARTER_CSS):
            self.assertIn(f'data-check-id="{check["id"]}"', page)
        self.assertEqual(page.count('data-hint-level="3"'), TOTAL_CHECKS)

    def test_arena_never_ships_the_answers_to_the_browser(self):
        page = self.client.get(reverse('home')).content.decode()
        for answer in ('.features__grid {\n  display: grid;\n'
                       '  grid-template-columns: repeat(3, 1fr);',
                       '.hero__title {\n  font-size: clamp(2.4rem, 4.4vw, 3.6rem);',
                       '@media (max-width: 860px)'):
            self.assertNotIn(answer, page, answer)
        for defect in ('display: block', 'font-size: 1rem', 'gap: 150px',
                       'rotate(45deg)', '@media (min-width: 860px)'):
            self.assertIn(defect, page, defect)

    def test_arena_requires_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)
        self.assertEqual(self.client.get(reverse('start')).status_code, 302)



class FinalPreviewTests(TestCase):
    """The final preview is a visual reference: it shows, it never grades."""

    def setUp(self):
        self.user = User.objects.create_user(username='Tester', pc_no='PC-FP', password='pw-123456')
        self.client.force_login(self.user, backend='first.backends.PCNoBackend')
        self.client.get(reverse('home'))  # start the clock
        self.url = reverse('api_final_preview')

    def fetch(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response

    # 1. renders CHALLENGE_HTML + solution.css
    def test_it_renders_the_challenge_markup_with_the_gold_standard(self):
        body = self.fetch().content.decode()

        # the finished page, in full
        for section in ('id="features"', 'id="pricing"', 'id="faq"',
                        'id="testimonials"', 'id="how-it-works"', 'site-footer'):
            self.assertIn(section, body, section)
        self.assertIn('<h1 class="hero__title">', body)

        # ...styled by the gold standard, inlined in place of the link tag
        self.assertIn('<style>', body)
        self.assertNotIn('href="style.css"', body)
        for correct in ('line-height: 1.6;',
                        'grid-template-columns: repeat(3, 1fr);',
                        '@media (max-width: 860px)'):
            self.assertIn(correct, body, correct)

    # 2. does not use the player's CSS
    def test_it_ignores_whatever_the_player_has_written(self):
        self.client.post(reverse('save_css'), {'css': 'body { background: fuchsia; }'})
        body = self.fetch().content.decode()
        self.assertNotIn('fuchsia', body)
        self.assertIn('line-height: 1.6;', body)

        # ...and the player's own stylesheet is untouched by the visit
        self.assertEqual(self.client.get(reverse('get_css')).json()['css'],
                         'body { background: fuchsia; }')

    # 3. does not modify objective progress
    def test_it_never_grades_or_advances_progress(self):
        before = self.client.post(reverse('api_check'), {'css': STARTER_CSS}).json()
        self.assertEqual(before['passed'], 0)

        self.fetch()
        self.fetch()

        self.user.refresh_from_db()
        self.assertEqual(self.user.best_score, 0)
        self.assertIsNone(self.user.completed_at)
        self.assertFalse(self.client.get(reverse('api_state')).json()['completed'])

    # 4. does not modify the player's CSS
    def test_it_leaves_the_saved_stylesheet_alone(self):
        mine = STARTER_CSS + '\n/* my working notes */\n'
        self.client.post(reverse('save_css'), {'css': mine})
        self.fetch()
        self.assertEqual(self.client.get(reverse('get_css')).json()['css'], mine)

    # 5. does not reveal source code
    def test_the_arena_page_never_carries_the_answers(self):
        arena = self.client.get(reverse('home')).content.decode()
        # anchored to the rule each answer belongs to: `line-height: 1.6` on its
        # own is legitimately still there, for `.testimonial-card__quote`.
        for answer in ('  color: var(--color-text);\n  line-height: 1.6;',
                       '.features__grid {\n  display: grid;\n'
                       '  grid-template-columns: repeat(3, 1fr);',
                       '@media (max-width: 860px)'):
            self.assertNotIn(answer, arena, answer)
        # the arena links to the preview rather than embedding it
        self.assertIn(self.url, arena)

    def test_there_is_no_endpoint_serving_the_raw_stylesheet(self):
        body = self.fetch().content.decode()
        # the gold standard only ever arrives inside a rendered page
        self.assertTrue(body.lstrip().startswith('<!DOCTYPE html>'))
        self.assertNotEqual(body.strip(), SOLUTION_CSS.strip())

    def test_it_requires_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    # 6. does not touch the timer
    def test_it_does_not_start_extend_or_reset_the_clock(self):
        self.user.refresh_from_db()
        started = self.user.game_start_time
        remaining = self.client.get(reverse('api_state')).json()['remaining']

        self.fetch()

        self.user.refresh_from_db()
        self.assertEqual(self.user.game_start_time, started)
        self.assertLessEqual(self.client.get(reverse('api_state')).json()['remaining'], remaining)

    def test_it_does_not_start_the_clock_for_someone_who_never_entered(self):
        fresh = User.objects.create_user(username='Fresh', pc_no='PC-FP2', password='pw-123456')
        self.client.force_login(fresh, backend='first.backends.PCNoBackend')
        self.fetch()
        fresh.refresh_from_db()
        self.assertIsNone(fresh.game_start_time, 'the reference view must not start a round')

    def test_it_still_opens_after_the_session_expires(self):
        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])
        self.assertEqual(self.client.get(self.url).status_code, 200)

    # 7. isolated from the game shell
    def test_it_is_served_as_its_own_document_for_a_sandboxed_frame(self):
        response = self.fetch()
        self.assertTrue(response['Content-Type'].startswith('text/html'))
        self.assertEqual(response['Cache-Control'], 'no-store')

        # The site denies framing everywhere else; this one view must allow the
        # arena to frame it, or the overlay silently renders an empty box.
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(self.client.get(reverse('home'))['X-Frame-Options'], 'DENY')

        body = response.content.decode()
        # nothing from the game shell leaks in, and nothing of it leaks out
        self.assertNotIn('wf-arena', body)
        self.assertNotIn('wf-base.css', body)
        self.assertNotIn('<script', body)

        arena = self.client.get(reverse('home')).content.decode()
        self.assertIn('id="wf-final-frame"', arena)
        # the frame that shows it carries an empty sandbox, like the main preview
        self.assertIn('class="wf-preview" title="The finished NovaCloud page" sandbox', arena)

    # 8. it can be closed and the arena is still there
    def test_the_overlay_ships_with_a_way_out(self):
        arena = self.client.get(reverse('home')).content.decode()
        self.assertIn('id="wf-final-open"', arena)
        self.assertIn('id="wf-final-close"', arena)
        self.assertIn('id="wf-final"', arena)
        # it is an overlay on the live arena, not a separate page
        self.assertIn('hidden role="dialog"', arena)
        self.assertIn('id="wf-css"', arena)
        self.assertIn('id="wf-run"', arena)


class HintQualityTests(TestCase):
    """Hints must narrow the search in three steps, never paste the answer.

    Hint 1 = the idea, hint 2 = where to look, hint 3 = which property.
    """

    SELECTOR = re.compile(r'`[.#][\w_-]+`')
    PROPERTY = re.compile(r'`[a-z-]+`')

    @staticmethod
    def finished_declarations():
        """Every declaration the player has to end up with, as literal text."""
        answers = []
        for pairs in GRADED_FIXES.values():
            for broken, fixed in pairs:
                already = {l.strip().rstrip(';') for l in broken.splitlines() if ':' in l}
                for line in fixed.splitlines():
                    declaration = line.strip().rstrip(';')
                    if ':' in declaration and declaration not in already:
                        answers.append(declaration)
                if fixed.strip().startswith('@media'):
                    answers.append(fixed.strip().rstrip(' {'))
        return answers

    def objectives(self):
        return [(c['id'], c['description'], c['hints'])
                for c in run_checks(CHALLENGE_HTML, STARTER_CSS)]

    def test_every_objective_has_exactly_three_hints(self):
        for objective, _description, hints in self.objectives():
            self.assertEqual(len(hints), 3, objective)
            for level, hint in enumerate(hints, start=1):
                self.assertTrue(hint.strip(), f'{objective} hint {level} is empty')

    def test_hint_one_teaches_the_idea_without_naming_the_code(self):
        for objective, _description, hints in self.objectives():
            self.assertNotRegex(hints[0], r'`[.#@][\w-]+', f'{objective}: hint 1 names a selector')

    def test_hint_two_says_where_to_look(self):
        for objective, _description, hints in self.objectives():
            located = (self.SELECTOR.search(hints[1])
                       or '@media' in hints[1]
                       or '`body`' in hints[1])
            self.assertTrue(located, f'{objective}: hint 2 points at no rule')

    def test_hint_three_names_the_property(self):
        for objective, _description, hints in self.objectives():
            self.assertRegex(hints[2], self.PROPERTY.pattern,
                             f'{objective}: hint 3 names no property')

    def test_no_hint_ever_pastes_a_finished_declaration(self):
        answers = self.finished_declarations()
        self.assertGreaterEqual(len(answers), TOTAL_CHECKS)
        for objective, description, hints in self.objectives():
            text = ' '.join(hints) + ' ' + description
            for answer in answers:
                self.assertNotIn(answer, text, f'{objective} gives away {answer!r}')

    def test_hints_stay_short_enough_to_read_at_a_glance(self):
        for objective, _description, hints in self.objectives():
            for level, hint in enumerate(hints, start=1):
                self.assertLessEqual(len(hint), 420, f'{objective} hint {level} is a paragraph')

    def test_the_two_masked_objectives_warn_the_player(self):
        """Fixing these changes nothing on screen until the breakpoint is fixed.

        Measured at 1120px: `.hero__container` keeps a single 1072px track and
        `.navbar__menu` stays opacity:0 / position:fixed, both because the
        misfiring 860px block still applies. Without a note the player thinks
        their correct edit failed.
        """
        hints = {objective: h for objective, _d, h in self.objectives()}
        for objective in ('css-hero-split', 'css-nav-spacing'):
            self.assertIn('responsive objective', ' '.join(hints[objective]).lower(),
                          f'{objective} needs the interaction note')
        # ...and the note must not leak the breakpoint's own answer
        for objective in ('css-hero-split', 'css-nav-spacing'):
            self.assertNotIn('max-width', ' '.join(hints[objective]))

    def test_the_console_hint_disambiguates_every_other_rotation(self):
        """`rotate(45deg)` appears three times; only `.console` is wrong.

        The other two are correct: the FAQ's open icon and the mobile menu
        button's cross. A player who searches the file must be told, or they
        will "fix" a rule that was never broken.
        """
        decoys = STARTER_CSS.count('rotate(45deg)')
        self.assertEqual(decoys, 3)
        hints = ' '.join({o: h for o, _d, h in self.objectives()}['css-console'])
        self.assertIn('.console', hints)
        self.assertIn('three times', hints, 'the hint must state how many there are')
        self.assertIn('FAQ', hints)

    def test_no_hint_asks_for_html_or_javascript(self):
        for objective, description, hints in self.objectives():
            text = (' '.join(hints) + ' ' + description).lower()
            for forbidden in ('javascript', 'index.html', '<div', '<span', 'markup'):
                self.assertNotIn(forbidden, text, f'{objective} mentions {forbidden}')

class PresenceTests(TransactionTestCase):
    """The live player count is per browser session, not per socket.

    Sessions are created up front: they are database writes, and the async
    bodies below cannot touch the ORM directly. Frames are read by exact
    count rather than "until it goes quiet" -- letting a read time out makes
    asgiref cancel the consumer under us.
    """

    # Frames a socket receives about itself when it connects: welcome + count.
    OWN_CONNECT_FRAMES = 2

    def setUp(self):
        first_presence._store = None  # a fresh store per test

    def tearDown(self):
        first_presence._store = None

    @staticmethod
    def new_session():
        session = SessionStore()
        session.create()
        return session.session_key

    @staticmethod
    def socket(session_key):
        """A presence socket carrying `session_key` the way a browser would."""
        cookie = f'{settings.SESSION_COOKIE_NAME}={session_key}'.encode()
        return WebsocketCommunicator(
            presence_application, '/ws/presence/', headers=[(b'cookie', cookie)],
        )

    @classmethod
    async def read(cls, communicator, frames):
        """Read exactly `frames` messages; return the last count seen."""
        latest = None
        for _ in range(frames):
            message = await communicator.receive_json_from(timeout=3)
            if 'count' in message:
                latest = message['count']
        return latest

    @classmethod
    async def join(cls, communicator):
        connected, _ = await communicator.connect()
        assert connected
        return await cls.read(communicator, cls.OWN_CONNECT_FRAMES)

    def test_connect_receives_a_welcome_and_a_count(self):
        session = self.new_session()

        async def run():
            socket = self.socket(session)
            connected, _ = await socket.connect()
            self.assertTrue(connected)

            welcome = await socket.receive_json_from(timeout=3)
            self.assertEqual(welcome['type'], 'welcome')
            self.assertEqual(welcome['heartbeat'], HEARTBEAT_INTERVAL_SECONDS)

            broadcast = await socket.receive_json_from(timeout=3)
            self.assertEqual(broadcast['count'], 1)
            await socket.disconnect()

        async_to_sync(run)()

    def test_tabs_of_one_session_count_as_one_player(self):
        mine, stranger_key, returning_key = (
            self.new_session(), self.new_session(), self.new_session())

        async def run():
            store = get_presence_store()

            tab_one = self.socket(mine)
            self.assertEqual(await self.join(tab_one), 1)

            tab_two = self.socket(mine)
            self.assertEqual(await self.join(tab_two), 1)
            self.assertEqual(await store.count(), 1, 'a second tab is not a second player')

            stranger = self.socket(stranger_key)
            self.assertEqual(await self.join(stranger), 2)

            # closing one tab of a two-tab session leaves that player online
            await tab_two.disconnect()
            self.assertEqual(await store.count(), 2)

            await stranger.disconnect()
            self.assertEqual(await store.count(), 1)

            # ...and a visitor can come back
            returning = self.socket(returning_key)
            self.assertEqual(await self.join(returning), 2)

            await tab_one.disconnect()
            await returning.disconnect()
            self.assertEqual(await store.count(), 0)

        async_to_sync(run)()

    def test_everyone_is_told_when_the_count_changes(self):
        watcher_key, other_key = self.new_session(), self.new_session()

        async def run():
            watcher = self.socket(watcher_key)
            self.assertEqual(await self.join(watcher), 1)

            other = self.socket(other_key)
            await other.connect()
            # the watcher is told about the new player
            self.assertEqual(await self.read(watcher, 1), 2)

            await other.disconnect()
            self.assertEqual(await self.read(watcher, 1), 1)
            await watcher.disconnect()

        async_to_sync(run)()

    def test_a_ping_answers_with_the_current_count(self):
        session = self.new_session()

        async def run():
            socket = self.socket(session)
            await self.join(socket)

            await socket.send_json_to({'type': 'ping'})
            pong = await socket.receive_json_from(timeout=3)
            self.assertEqual(pong['type'], 'pong')
            self.assertEqual(pong['count'], 1)
            await socket.disconnect()

        async_to_sync(run)()


class HintMarkupTests(TestCase):
    """Backtick spans in a hint render as <code>, and only as <code>."""

    def setUp(self):
        self.user = User.objects.create_user(username='Tester', pc_no='PC-HM', password='pw-123456')
        self.client.force_login(self.user, backend='first.backends.PCNoBackend')

    def test_backticks_become_code_elements(self):
        from .templatetags.wf_hints import code_spans
        self.assertEqual(code_spans('Check `display` on `.navbar`.'),
                         'Check <code>display</code> on <code>.navbar</code>.')

    def test_the_filter_escapes_before_it_marks_anything_safe(self):
        from .templatetags.wf_hints import code_spans
        rendered = code_spans('<script>alert(1)</script> and `<b>x</b>`')
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('<code>&lt;b&gt;x&lt;/b&gt;</code>', rendered)

    def test_the_arena_renders_hint_code_spans(self):
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn('<code>.navbar</code>', page)
        self.assertNotIn('`.navbar`', page)

    def test_each_hint_is_labelled_with_its_level(self):
        page = self.client.get(reverse('home')).content.decode()
        for label in ('the idea', 'where to look', 'which property'):
            self.assertEqual(page.count(label), TOTAL_CHECKS, label)

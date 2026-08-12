"""
Regression tests for the challenge itself.

The important properties: the shipped page fails every graded objective, the
gold standard passes every one, each graded fix clears exactly its own box,
equivalent beginner answers are accepted, and the clock cannot be restarted.

Note that `style.css` ships 39 deliberate defects while only 14 are graded, so
"apply the graded fixes" produces a *passing* submission, not a pristine one.
`solution.css` remains the organisers' reference.
"""

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
    GAME_DURATION_SECONDS,
    GRADED_CSS_FIXES,
    GRADED_FIXES,
    GRADED_HTML_FIXES,
    SOLUTION_CSS,
    SOLUTION_HTML,
    STARTER_CSS,
    STARTER_HTML,
    apply_fixes,
)
from .models import User
from .presence import HEARTBEAT_INTERVAL_SECONDS, get_presence_store


def reference_solution():
    """The gold-standard submission: every defect repaired."""
    return SOLUTION_HTML, SOLUTION_CSS


def graded_solution():
    """The minimum passing submission: only the 14 graded objectives fixed."""
    return (apply_fixes(STARTER_HTML, GRADED_HTML_FIXES),
            apply_fixes(STARTER_CSS, GRADED_CSS_FIXES))


def results_by_id(html, css):
    return {r['id']: r['passed'] for r in run_checks(html, css)}


class ChallengeSourceTests(TestCase):
    """The four challenge files must stay consistent with the fix table."""

    def test_every_graded_fix_anchors_uniquely(self):
        # apply_fixes raises when an anchor is missing or ambiguous, so this
        # fails loudly the moment a challenge file drifts from the table.
        apply_fixes(STARTER_HTML, GRADED_HTML_FIXES)
        apply_fixes(STARTER_CSS, GRADED_CSS_FIXES)

    def test_there_is_one_graded_fix_per_objective(self):
        self.assertEqual(len(GRADED_HTML_FIXES), HTML_CHECKS)
        self.assertEqual(len(GRADED_CSS_FIXES), CSS_CHECKS)
        self.assertEqual(len(GRADED_FIXES), TOTAL_CHECKS)
        self.assertEqual({d['id'] for d in run_checks('', '')},
                         set(GRADED_FIXES))

    def test_the_round_stays_beginner_sized(self):
        # 12-16 objectives, mostly CSS. A wider brief would stop being a
        # 30 minute beginner round, so lock the shape in.
        self.assertTrue(12 <= TOTAL_CHECKS <= 16, TOTAL_CHECKS)
        self.assertGreaterEqual(CSS_CHECKS / TOTAL_CHECKS, 0.7)

    def test_every_objective_ships_three_hints(self):
        for check in run_checks(STARTER_HTML, STARTER_CSS):
            self.assertEqual(len(check['hints']), 3, check['id'])
            self.assertTrue(check['description'].strip(), check['id'])

    def test_the_starter_is_the_finished_novacloud_page(self):
        # The player repairs a complete site; they never build one.
        self.assertIn('NovaCloud', STARTER_HTML)
        self.assertIn('<!DOCTYPE html>', STARTER_HTML)
        for section in ('id="features"', 'id="pricing"', 'id="faq"',
                        'id="testimonials"', 'id="how-it-works"', 'site-footer'):
            self.assertIn(section, STARTER_HTML, section)
        self.assertGreater(len(STARTER_HTML), 20000)
        self.assertGreater(len(STARTER_CSS), 20000)

    def test_the_page_renders_without_javascript(self):
        # The preview sandbox blocks scripts, so nothing may depend on them.
        self.assertNotIn('<script', STARTER_HTML)
        self.assertNotIn('class="reveal"', STARTER_HTML)
        self.assertNotIn('reveal--visible', STARTER_HTML)

    def test_the_shipped_stylesheet_carries_more_noise_than_objectives(self):
        # A deliberate design decision: 39 defects ship, 14 are scored. If the
        # two ever converge this assertion is the place to revisit it.
        differing = sum(1 for a, b in zip(SOLUTION_CSS.splitlines(),
                                          STARTER_CSS.splitlines()) if a != b)
        self.assertGreater(differing, TOTAL_CHECKS)


class ChallengeCheckTests(TestCase):
    def test_starter_page_fails_every_objective(self):
        results = run_checks(STARTER_HTML, STARTER_CSS)
        self.assertEqual(len(results), TOTAL_CHECKS)
        self.assertEqual([r['id'] for r in results if r['passed']], [])

    def test_gold_standard_passes_every_objective(self):
        html, css = reference_solution()
        failed = [r['id'] for r in run_checks(html, css) if not r['passed']]
        self.assertEqual(failed, [])

    def test_fixing_only_the_graded_objectives_is_enough_to_win(self):
        html, css = graded_solution()
        failed = [r['id'] for r in run_checks(html, css) if not r['passed']]
        self.assertEqual(failed, [])
        # ...and that submission is deliberately not the gold standard
        self.assertNotEqual(css, SOLUTION_CSS)

    def test_each_fix_clears_exactly_its_own_objective(self):
        """Fixing one thing must not accidentally tick a different box."""
        for objective, pairs in GRADED_CSS_FIXES.items():
            css = apply_fixes(STARTER_CSS, pairs)
            passed = [k for k, v in results_by_id(STARTER_HTML, css).items() if v]
            self.assertEqual(passed, [objective])

        for objective, pairs in GRADED_HTML_FIXES.items():
            html = apply_fixes(STARTER_HTML, pairs)
            passed = [k for k, v in results_by_id(html, STARTER_CSS).items() if v]
            self.assertEqual(passed, [objective])

    def test_alternative_but_valid_answers_are_accepted(self):
        html, css = graded_solution()

        auto_fit = css.replace('grid-template-columns: repeat(3, 1fr);',
                               'grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));', 1)
        self.assertTrue(results_by_id(html, auto_fit)['css-features'])

        three_tracks = css.replace('grid-template-columns: repeat(3, 1fr);',
                                   'grid-template-columns: 1fr 1fr 1fr;', 1)
        self.assertTrue(results_by_id(html, three_tracks)['css-features'])

        # a plain size instead of the design system's clamp()
        plain_size = css.replace('font-size: clamp(2.4rem, 4.4vw, 3.6rem);',
                                 'font-size: 56px;')
        self.assertTrue(results_by_id(html, plain_size)['css-hero-title'])

        # inline-flex is as good as flex for the header bar
        inline_flex = css.replace('.navbar {\n  display: flex;',
                                  '.navbar {\n  display: inline-flex;')
        self.assertTrue(results_by_id(html, inline_flex)['css-navbar-row'])

        # rem gaps, and a literal radius instead of the token
        rem_gap = css.replace('  gap: 32px;\n  flex: 1;', '  gap: 2rem;\n  flex: 1;')
        self.assertTrue(results_by_id(html, rem_gap)['css-nav-spacing'])

        literal_radius = css.replace('  border-radius: var(--radius-md);\n  padding: 32px;',
                                     '  border-radius: 16px;\n  padding: 2rem;')
        self.assertTrue(results_by_id(html, literal_radius)['css-feature-box'])

        # deleting the tilt is as good as reducing it
        for value in ('rotate(0deg)', 'none'):
            upright = STARTER_CSS.replace('  overflow: hidden;\n  transform: rotate(45deg);',
                                          '  overflow: hidden;\n  transform: ' + value + ';')
            self.assertTrue(results_by_id(STARTER_HTML, upright)['css-console'], value)

        # the stat numbers may be written with or without a thousands separator
        plain_numbers = html.replace('>12,000<', '>12000<')
        self.assertTrue(results_by_id(plain_numbers, css)['html-stats'])

    def test_formatting_noise_does_not_affect_grading(self):
        html, css = graded_solution()
        noisy = css.replace('.navbar {\n  display: flex;\n  align-items: center;',
                            '.navbar{align-items:center;display:flex;  /* tidied */')
        self.assertTrue(results_by_id(html, noisy)['css-navbar-row'])

    def test_the_faq_icon_rotation_is_not_mistaken_for_the_console(self):
        # `.faq-item--open .faq-item__icon` legitimately uses rotate(45deg);
        # it must not be read as the console's transform, in either direction.
        self.assertIn('.faq-item--open .faq-item__icon', STARTER_CSS)
        html, css = graded_solution()
        self.assertTrue(results_by_id(html, css)['css-console'])
        self.assertFalse(results_by_id(STARTER_HTML, STARTER_CSS)['css-console'])

    def test_hover_rules_do_not_satisfy_base_selectors(self):
        css = STARTER_CSS.replace('.navbar__link:hover {\n  color: var(--color-text);',
                                  '.navbar__link:hover {\n  color: var(--color-text);\n'
                                  '  display: flex;')
        self.assertFalse(results_by_id(STARTER_HTML, css)['css-navbar-row'])

    def test_media_query_answers_are_scoped_to_the_media_query(self):
        html, css = graded_solution()
        # a desktop two-column hero with no narrow rule is not responsive
        stripped = css.replace('@media (max-width: 860px) {', '@media (min-width: 861px) {')
        self.assertFalse(results_by_id(html, stripped)['css-responsive'])
        self.assertTrue(results_by_id(html, stripped)['css-hero-split'])

    def test_malformed_submission_does_not_raise(self):
        results = run_checks('<div><p>unclosed', 'body { color: ; } @media { .x {')
        self.assertEqual(len(results), TOTAL_CHECKS)

    def test_hostile_css_is_graded_without_crashing(self):
        hostile = '* { all: unset !important; } body { background: red !important; }'
        results = results_by_id(STARTER_HTML, hostile)
        self.assertEqual(len(results), TOTAL_CHECKS)
        # Deleting the stylesheet does not solve the round. (`css-console`
        # legitimately passes: with no transform declared, nothing is rotated.)
        for objective in ('css-line-height', 'css-navbar-row', 'css-nav-spacing',
                          'css-hero-split', 'css-hero-title', 'css-hero-gap',
                          'css-features', 'css-feature-box', 'css-responsive'):
            self.assertFalse(results[objective], objective)


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

        # a refresh must not move the start time
        self.client.get(reverse('home'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.game_start_time, started)

    def test_state_endpoint_reports_the_full_duration_at_the_start(self):
        self.client.get(reverse('home'))
        data = self.client.get(reverse('api_state')).json()
        self.assertEqual(data['duration'], GAME_DURATION_SECONDS)
        self.assertGreater(data['remaining'], GAME_DURATION_SECONDS - 10)
        self.assertFalse(data['expired'])

    def test_reset_restores_the_broken_page_without_touching_the_clock(self):
        self.client.get(reverse('home'))
        html, css = graded_solution()
        self.client.post(reverse('save_css'), {'html': html, 'css': css})

        self.user.refresh_from_db()
        started = self.user.game_start_time

        data = self.client.post(reverse('api_reset'), {}).json()
        self.assertEqual(data['html'], STARTER_HTML)
        self.assertEqual(data['css'], STARTER_CSS)

        self.user.refresh_from_db()
        self.assertEqual(self.user.game_start_time, started)

    def test_expired_session_refuses_saves_and_checks(self):
        self.client.get(reverse('home'))
        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])

        html, css = graded_solution()
        saved = self.client.post(reverse('save_css'), {'html': 'x', 'css': 'y'}).json()
        self.assertEqual(saved['error'], 'Time is up!')

        checked = self.client.post(reverse('api_check'), {'html': html, 'css': css}).json()
        self.assertEqual(checked['error'], 'Time is up!')
        self.assertFalse(checked['completed'])

        self.user.refresh_from_db()
        self.assertIsNone(self.user.completed_at)
        self.assertNotEqual(self.client.get(reverse('get_css')).json()['html'], 'x')

    def test_expired_session_also_refuses_reset(self):
        self.client.get(reverse('home'))
        self.client.post(reverse('save_css'), {'html': 'mine', 'css': 'mine'})
        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])

        data = self.client.post(reverse('api_reset'), {}).json()
        self.assertNotIn('html', data)
        self.assertEqual(self.client.get(reverse('get_css')).json()['html'], 'mine')

    def test_solving_the_challenge_completes_and_locks_the_session(self):
        self.client.get(reverse('home'))
        html, css = graded_solution()
        data = self.client.post(reverse('api_check'), {'html': html, 'css': css}).json()

        self.assertEqual(data['passed'], TOTAL_CHECKS)
        self.assertTrue(data['completed'])
        self.assertTrue(data['locked'])

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.completed_at)
        self.assertEqual(self.user.best_score, TOTAL_CHECKS)

    def test_partial_progress_is_recorded_as_a_best_score(self):
        self.client.get(reverse('home'))
        some = dict(list(GRADED_CSS_FIXES.items())[:3])
        css = apply_fixes(STARTER_CSS, some)
        data = self.client.post(reverse('api_check'), {'html': STARTER_HTML, 'css': css}).json()

        self.assertEqual(data['passed'], 3)
        self.assertFalse(data['completed'])

        # a worse follow-up submission must not lower the recorded best
        self.client.post(reverse('api_check'), {'html': STARTER_HTML, 'css': STARTER_CSS})
        self.user.refresh_from_db()
        self.assertEqual(self.user.best_score, 3)

    def test_autosaved_work_counts_even_without_running_the_checks(self):
        self.client.get(reverse('home'))
        some = dict(list(GRADED_CSS_FIXES.items())[:4])
        css = apply_fixes(STARTER_CSS, some)
        self.client.post(reverse('save_css'), {'html': STARTER_HTML, 'css': css})

        self.client.get(reverse('home'))  # grading happens on render
        self.user.refresh_from_db()
        self.assertEqual(self.user.best_score, 4)

    def test_running_out_of_time_closes_the_round_even_on_a_perfect_page(self):
        self.client.get(reverse('home'))
        html, css = graded_solution()
        self.client.post(reverse('save_css'), {'html': html, 'css': css})

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
        self.assertIn(str(TOTAL_CHECKS), page)
        self.assertIn('Round 01', page)

    def test_arena_renders_the_broken_page_and_every_objective(self):
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn('wf-preview', page)
        self.assertIn('sandbox', page)
        for check in run_checks(STARTER_HTML, STARTER_CSS):
            self.assertIn(f'data-check-id="{check["id"]}"', page)
        # three hints per objective, all hidden until the player asks
        self.assertEqual(page.count('data-hint-level="3"'), TOTAL_CHECKS)

    def test_arena_never_ships_the_answers_to_the_browser(self):
        page = self.client.get(reverse('home')).content.decode()
        # anchored to the rule each answer belongs to: `repeat(3, 1fr)` on its
        # own is legitimately still in the file, for `.pricing__grid`.
        for answer in ('.features__grid {\n  display: grid;\n'
                       '  grid-template-columns: repeat(3, 1fr);',
                       '.hero__title {\n  font-size: clamp(2.4rem, 4.4vw, 3.6rem);',
                       '@media (max-width: 860px)'):
            self.assertNotIn(answer, page, answer)
        # ...while the defects the player must notice are all present
        for defect in ('display: block', 'font-size: 1rem', 'gap: 150px',
                       'rotate(45deg)', '@media (min-width: 860px)'):
            self.assertIn(defect, page, defect)

    def test_arena_requires_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)
        self.assertEqual(self.client.get(reverse('start')).status_code, 302)


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

"""
Regression tests for the challenge itself.

The important properties: the shipped page fails every objective, the intended
fix passes every objective, and the clock cannot be restarted by the player.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .checks import TOTAL_CHECKS, run_checks
from .game_config import GAME_DURATION_SECONDS, STARTER_CSS, STARTER_HTML
from .models import User


def reference_solution():
    """Apply the fix the objectives describe, one edit per objective."""
    html = STARTER_HTML
    html = html.replace('<h2 class="hero-title">', '<h1 class="hero-title">')
    html = html.replace('compiles.</h2>', 'compiles.</h1>')
    html = html.replace('<a href="#plans">Plans</a>',
                        '<a href="#plans">Plans</a>\n    <a href="#contact">Contact</a>')
    html = html.replace('src="/static/img/hero-cup.svg">',
                        'src="/static/img/hero-cup.svg" alt="A cup of Pixel Perk coffee">')
    html = html.replace('<div class="btn btn-primary">Order a bag</div>',
                        '<button class="btn btn-primary">Order a bag</button>')
    html = html.replace('<span class="card-icon">\U0001F69A</span>\n',
                        '<span class="card-icon">\U0001F69A</span>\n'
                        '      <h3 class="card-title">Free delivery</h3>\n')
    html = html.replace('<article>\n      <span class="card-icon">♻',
                        '<article class="card">\n      <span class="card-icon">♻')
    html += '\n<footer class="site-footer">© 2026 Pixel Perk Coffee</footer>\n'

    css = STARTER_CSS
    css = css.replace('body {\n  margin: 0;', 'body {\n  margin: 0;\n  font-family: Arial, sans-serif;')
    css = css.replace('.site-nav {\n  display: block;', '.site-nav {\n  display: flex;')
    css = css.replace('text-align: centre;', 'text-align: center;')
    css = css.replace('.hero-title {\n  font-size: 12px;', '.hero-title {\n  font-size: 46px;')
    css = css.replace('grid-template-columns: 1fr;', 'grid-template-columns: repeat(3, 1fr);')
    css = css.replace('background-color: #b45309;\n  color: #b45309;',
                      'background-color: #b45309;\n  color: #ffffff;')
    css = css.replace('  .card-grid { grid-template-columns: repeat(3, 1fr); }',
                      '  .card-grid { grid-template-columns: 1fr; }')
    return html, css


class ChallengeCheckTests(TestCase):
    def test_starter_page_fails_every_objective(self):
        results = run_checks(STARTER_HTML, STARTER_CSS)
        self.assertEqual(len(results), TOTAL_CHECKS)
        self.assertEqual([r['id'] for r in results if r['passed']], [])

    def test_reference_solution_passes_every_objective(self):
        html, css = reference_solution()
        failed = [r['id'] for r in run_checks(html, css) if not r['passed']]
        self.assertEqual(failed, [])

    def test_alternative_but_valid_answers_are_accepted(self):
        html, css = reference_solution()

        auto_fit = css.replace('grid-template-columns: repeat(3, 1fr);',
                               'grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));')
        results = {r['id']: r['passed'] for r in run_checks(html, auto_fit)}
        self.assertTrue(results['grid-columns'])
        self.assertTrue(results['responsive'])

        three_tracks = css.replace('grid-template-columns: repeat(3, 1fr);',
                                   'grid-template-columns: 1fr 1fr 1fr;')
        self.assertTrue({r['id']: r['passed'] for r in run_checks(html, three_tracks)}['grid-columns'])

        rem_title = css.replace('font-size: 46px;', 'font-size: 3rem;')
        self.assertTrue({r['id']: r['passed'] for r in run_checks(html, rem_title)}['hero-title'])

        named_colour = css.replace('color: #ffffff;', 'color: white;')
        self.assertTrue({r['id']: r['passed'] for r in run_checks(html, named_colour)}['btn-contrast'])

    def test_hover_rules_do_not_satisfy_base_selectors(self):
        # .btn:hover changing the background must not count as fixing .btn
        css = STARTER_CSS.replace('.btn:hover { background-color: #92400e; }',
                                  '.btn:hover { background-color: #92400e; color: #fff; }')
        results = {r['id']: r['passed'] for r in run_checks(STARTER_HTML, css)}
        self.assertFalse(results['btn-contrast'])

    def test_malformed_submission_does_not_raise(self):
        results = run_checks('<div><p>unclosed', 'body { color: ; } @media { .x {')
        self.assertEqual(len(results), TOTAL_CHECKS)


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

    def test_expired_session_refuses_saves_and_checks(self):
        self.client.get(reverse('home'))
        self.user.game_start_time = timezone.now() - timedelta(seconds=GAME_DURATION_SECONDS + 1)
        self.user.save(update_fields=['game_start_time'])

        html, css = reference_solution()
        saved = self.client.post(reverse('save_css'), {'html': 'x', 'css': 'y'}).json()
        self.assertEqual(saved['error'], 'Time is up!')

        checked = self.client.post(reverse('api_check'), {'html': html, 'css': css}).json()
        self.assertEqual(checked['error'], 'Time is up!')
        self.assertFalse(checked['completed'])

        self.user.refresh_from_db()
        self.assertIsNone(self.user.completed_at)
        self.assertNotEqual(self.client.get(reverse('get_css')).json()['html'], 'x')

    def test_solving_the_challenge_completes_and_locks_the_session(self):
        self.client.get(reverse('home'))
        html, css = reference_solution()
        data = self.client.post(reverse('api_check'), {'html': html, 'css': css}).json()

        self.assertEqual(data['passed'], TOTAL_CHECKS)
        self.assertTrue(data['completed'])
        self.assertTrue(data['locked'])

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.completed_at)
        self.assertEqual(self.user.best_score, TOTAL_CHECKS)

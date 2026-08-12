from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .checks import CSS_CHECKS, HTML_CHECKS, TOTAL_CHECKS, run_checks
from .game_config import (
    DIFFICULTY,
    GAME_DURATION_SECONDS,
    ROUND_NUMBER,
    SITE_NAME,
    SITE_TAGLINE,
    STARTER_CSS,
    STARTER_HTML,
    TIMER_DANGER_SECONDS,
    TIMER_SYNC_INTERVAL_SECONDS,
    TIMER_WARNING_SECONDS,
)
from .models import CssRule, User

# Generous ceiling; the challenge page is ~12 KB of HTML and ~11 KB of CSS.
MAX_SUBMISSION_CHARS = 200_000

# Everything the templates need to name the current round in one place.
CHALLENGE = {
    'round': ROUND_NUMBER,
    'site': SITE_NAME,
    'tagline': SITE_TAGLINE,
    'difficulty': DIFFICULTY,
    'minutes': GAME_DURATION_SECONDS // 60,
    'objectives': TOTAL_CHECKS,
    'css_objectives': CSS_CHECKS,
    'html_objectives': HTML_CHECKS,
}


def _ensure_session(request):
    """Presence dedupes by session key, so make sure the visitor has one."""
    if not request.session.session_key:
        request.session.create()


def _get_submission(user):
    submission, _ = CssRule.objects.get_or_create(
        user=user,
        defaults={'html': STARTER_HTML, 'css': STARTER_CSS},
    )
    return submission


def _record_progress(user, passed):
    """Persist a new personal best, and completion once every objective is met.

    Completion is only awarded while the session is still live: running out of
    time closes the round whatever the last submission scores.
    """
    fields = []
    if passed > user.best_score:
        user.best_score = passed
        fields.append('best_score')
    if passed == TOTAL_CHECKS and not user.completed_at and not user.is_expired:
        user.completed_at = timezone.now()
        fields.append('completed_at')
    if fields:
        user.save(update_fields=fields)


def _state(user):
    return {
        'remaining': user.remaining_seconds,
        'duration': GAME_DURATION_SECONDS,
        'expired': user.is_expired,
        'completed': user.is_completed,
        'locked': user.is_locked,
        'score': user.best_score,
        'total': TOTAL_CHECKS,
    }


# ---------------------------------------------------------------- pages ----

def intro(request):
    """Game home page."""
    _ensure_session(request)
    return render(request, 'intro.html', {'challenge': CHALLENGE})


def start(request):
    """Short 'booting the arena' transition between auth and the challenge."""
    if not request.user.is_authenticated:
        return redirect('login')
    return render(request, 'start.html', {'challenge': CHALLENGE})


def home(request):
    """The challenge arena. Opening it for the first time starts the clock."""
    if not request.user.is_authenticated:
        return redirect('login')

    _ensure_session(request)
    request.user.start_challenge()
    submission = _get_submission(request.user)

    # Grade what is on disk before rendering. Autosaved work that the player
    # never explicitly ran the checks on still counts -- otherwise the end of
    # round summary can disagree with the ticks in the objectives panel.
    checks = run_checks(submission.html, submission.css)
    passed = sum(1 for check in checks if check['passed'])
    _record_progress(request.user, passed)

    state = _state(request.user)

    return render(request, 'entry.html', {
        'html': submission.html,
        'css': submission.css,
        'state': state,
        'checks': checks,
        'passed': passed,
        'challenge': CHALLENGE,
        'arena': {
            'state': state,
            'timer': {
                'warning': TIMER_WARNING_SECONDS,
                'danger': TIMER_DANGER_SECONDS,
                'sync': TIMER_SYNC_INTERVAL_SECONDS,
            },
            'urls': {
                'save': reverse('save_css'),
                'check': reverse('api_check'),
                'state': reverse('api_state'),
                'reset': reverse('api_reset'),
            },
        },
    })


# ----------------------------------------------------------------- auth ----

def user_signup(request):
    if request.user.is_authenticated:
        return redirect('start')

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        pc_no = (request.POST.get('pc_no') or '').strip()
        password = request.POST.get('password') or ''

        if not username or not pc_no or not password:
            return render(request, 'signup.html', {'error': 'All fields are required'})

        if User.objects.filter(pc_no=pc_no).exists():
            return render(request, 'signup.html', {'error': 'PC number already registered'})

        user = User.objects.create_user(username=username, pc_no=pc_no, password=password)
        user.backend = 'first.backends.PCNoBackend'
        login(request, user)
        return redirect('start')

    return render(request, 'signup.html')


def user_login(request):
    if request.user.is_authenticated:
        return redirect('start')

    if request.method == 'POST':
        pc_no = (request.POST.get('pc_no') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, pc_no=pc_no, password=password)
        if user:
            user.backend = 'first.backends.PCNoBackend'
            login(request, user)
            return redirect('start')
        return render(request, 'login.html', {'error': 'Invalid credentials'})

    return render(request, 'login.html')


def user_logout(request):
    logout(request)
    return redirect('login')


# ------------------------------------------------------------------ api ----

def _read_submission_payload(request):
    html = (request.POST.get('html') or '')[:MAX_SUBMISSION_CHARS]
    css = (request.POST.get('css') or '')[:MAX_SUBMISSION_CHARS]
    return html, css


@require_POST
def save_css(request):
    """Autosave. Refuses writes once the session is locked (time up / done)."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in'}, status=403)

    state = _state(request.user)
    if state['locked']:
        state['error'] = 'Time is up!' if state['expired'] else 'Challenge already completed.'
        return JsonResponse(state, status=200)

    html, css = _read_submission_payload(request)
    CssRule.objects.update_or_create(
        user=request.user, defaults={'html': html, 'css': css},
    )
    return JsonResponse(state)


def get_css(request):
    if not request.user.is_authenticated:
        return JsonResponse({'html': '', 'css': ''})
    submission = _get_submission(request.user)
    return JsonResponse({'html': submission.html, 'css': submission.css})


def api_state(request):
    """Authoritative countdown source. The browser only renders it."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in'}, status=403)
    return JsonResponse(_state(request.user))


@require_POST
def api_check(request):
    """Save, then grade the submission against the challenge objectives."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in'}, status=403)

    user = request.user
    if user.is_locked:
        submission = _get_submission(user)
        payload = _state(user)
        payload['checks'] = run_checks(submission.html, submission.css)
        payload['error'] = 'Time is up!' if user.is_expired else 'Challenge already completed.'
        return JsonResponse(payload)

    html, css = _read_submission_payload(request)
    CssRule.objects.update_or_create(user=user, defaults={'html': html, 'css': css})

    checks = run_checks(html, css)
    passed = sum(1 for check in checks if check['passed'])
    _record_progress(user, passed)

    payload = _state(user)
    payload['checks'] = checks
    payload['passed'] = passed
    return JsonResponse(payload)


@require_POST
def api_reset(request):
    """Put the broken page back. Does not touch the countdown."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in'}, status=403)
    if request.user.is_locked:
        return JsonResponse(_state(request.user))

    CssRule.objects.update_or_create(
        user=request.user, defaults={'html': STARTER_HTML, 'css': STARTER_CSS},
    )
    payload = _state(request.user)
    payload.update({'html': STARTER_HTML, 'css': STARTER_CSS})
    return JsonResponse(payload)

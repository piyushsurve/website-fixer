import re

from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db import IntegrityError, transaction
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .checks import HINT_LEVELS, TOTAL_CHECKS, hint_text, run_checks
from .game_config import (
    CHALLENGE_HTML,
    DIFFICULTY,
    GAME_DURATION_SECONDS,
    MODE,
    CHALLENGE_LABEL,
    SITE_NAME,
    SITE_TAGLINE,
    SOLUTION_CSS,
    STARTER_CSS,
    TIMER_DANGER_SECONDS,
    TIMER_SYNC_INTERVAL_SECONDS,
    TIMER_WARNING_SECONDS,
)
from .models import CssRule, FinalSubmission, HintReveal, User
from .templatetags.wf_hints import code_spans

# Generous ceiling; the shipped stylesheet is ~25 KB.
MAX_SUBMISSION_CHARS = 200_000

# Everything the templates need to name the current round in one place.
CHALLENGE = {
    'label': CHALLENGE_LABEL,
    'site': SITE_NAME,
    'tagline': SITE_TAGLINE,
    'difficulty': DIFFICULTY,
    'mode': MODE,
    'minutes': GAME_DURATION_SECONDS // 60,
    'objectives': TOTAL_CHECKS,
}


# Matches the challenge page's own <link rel="stylesheet" href="style.css">.
_STYLESHEET_LINK = re.compile(
    r"""<link\s[^>]*href\s*=\s*['"][^'"]*style[.]css['"][^>]*>""", re.I,
)


def _ensure_session(request):
    """Presence dedupes by session key, so make sure the visitor has one."""
    if not request.session.session_key:
        request.session.create()


def _get_submission(user):
    """The player's stylesheet. The markup is fixed and never stored per-user."""
    submission, _ = CssRule.objects.get_or_create(
        user=user,
        defaults={'html': '', 'css': STARTER_CSS},
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


def finalize_if_due(user):
    """Snapshot the round the moment the server says the deadline has passed.

    This runs from every authenticated entry point rather than from a
    scheduled job, so a player whose screen nobody is watching is still
    submitted correctly the next time anything touches their account -- and
    `submitted_at` is the true deadline, not whenever that happened to be.

    Written once. If a snapshot already exists it is returned untouched.
    """
    if not user.is_expired:
        return None

    existing = FinalSubmission.objects.filter(user=user).first()
    if existing:
        return existing

    submission = _get_submission(user)
    checks = run_checks(CHALLENGE_HTML, submission.css)
    score = sum(1 for check in checks if check['passed'])
    if score > user.best_score:
        user.best_score = score
        user.save(update_fields=['best_score'])

    try:
        with transaction.atomic():
            return FinalSubmission.objects.create(
                user=user,
                pc_no=user.pc_no,
                started_at=user.game_start_time,
                submitted_at=user.deadline,
                final_css=submission.css,
                score=user.best_score,
                total=TOTAL_CHECKS,
                reached_all=user.design_mode,
                design_mode=user.design_mode,
                eligible=user.is_eligible,
                hints_used=user.hints_used,
                objectives_hinted=user.objectives_hinted,
            )
    except IntegrityError:
        # Two requests raced across the deadline; the first one won.
        return FinalSubmission.objects.get(user=user)


def finalize_all_due():
    """Settle every round whose deadline has passed but was never revisited.

    A player who closes the laptop and walks away generates no further
    requests, so `finalize_if_due` never fires for them. The admin calls this
    when an organiser opens a participant list, which is the point at which
    somebody actually needs the entry to exist.

    Returns the number of submissions created.
    """
    due = User.objects.filter(
        game_start_time__isnull=False,
        final_submission__isnull=True,
        game_start_time__lte=timezone.now() - timezone.timedelta(
            seconds=GAME_DURATION_SECONDS),
    )
    return sum(1 for user in due if finalize_if_due(user) is not None)


def _state(user):
    """Authoritative round state. Every value here is computed server-side."""
    final = finalize_if_due(user)
    return {
        'remaining': user.remaining_seconds,
        'duration': GAME_DURATION_SECONDS,
        'expired': user.is_expired,
        'completed': user.design_mode,
        'designMode': user.design_mode,
        'locked': user.is_locked,
        'score': user.best_score,
        'total': TOTAL_CHECKS,
        'eligible': user.is_eligible,
        'hintsUsed': user.hints_used,
        'objectivesHinted': user.objectives_hinted,
        'maxHints': TOTAL_CHECKS * HINT_LEVELS,
        'submitted': final is not None,
        'submittedAt': final.submitted_at.isoformat() if final else None,
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
    finalize_if_due(request.user)
    submission = _get_submission(request.user)

    # Grade what is on disk before rendering. Autosaved work that the player
    # never explicitly ran the checks on still counts -- otherwise the end of
    # round summary can disagree with the ticks in the objectives panel.
    checks = run_checks(CHALLENGE_HTML, submission.css)
    passed = sum(1 for check in checks if check['passed'])
    _record_progress(request.user, passed)

    state = _state(request.user)

    return render(request, 'entry.html', {
        'html': CHALLENGE_HTML,
        'css': submission.css,
        'state': state,
        'checks': checks,
        'passed': passed,
        'challenge': CHALLENGE,
        'hint_levels': HINT_LEVELS,
        'arena': {
            'state': state,
            # Hints already paid for, so a refresh shows them again for free.
            'revealed': [
                {'objective': objective, 'level': level,
                 'html': code_spans(hint_text(objective, level) or '')}
                for objective, level in
                request.user.hint_reveals.values_list('objective', 'level')
            ],
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
                'hint': reverse('api_hint'),
                'finalPreview': reverse('api_final_preview'),
                'finalDesign': reverse('api_final_design'),
                'exit': reverse('logout'),
            },
        },
    })


def _render_novacloud(css):
    """The challenge markup rendered with `css`, for a sandboxed iframe.

    Used by both preview endpoints — the official solution and a player's own
    submitted design — so the two render through exactly the same path and
    differ only in which stylesheet goes in.

    The site sends X-Frame-Options: DENY everywhere else; these two views are
    relaxed to SAMEORIGIN because the arena and the admin frame them. They
    stay un-embeddable by any other origin, and the frames carry an empty
    `sandbox` so the CSS inside cannot reach the page around it.
    """
    document = _STYLESHEET_LINK.sub(
        lambda _: '<style>' + css + '</style>', CHALLENGE_HTML, count=1,
    )
    response = HttpResponse(document, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@require_POST
def api_hint(request):
    """Reveal one hint, and record that it was revealed.

    The count is kept here, not in the browser: a level is charged the first
    time it is opened and never again, so re-reading a hint is free and no
    amount of clicking (or a forged `hints_used`) can change the total.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in'}, status=403)

    user = request.user
    if user.is_locked:
        payload = _state(user)
        payload['error'] = 'Time is up!'
        return JsonResponse(payload, status=200)

    objective = (request.POST.get('objective') or '').strip()
    try:
        level = int(request.POST.get('level') or 0)
    except (TypeError, ValueError):
        level = 0

    text = hint_text(objective, level)
    if text is None:
        return JsonResponse({'error': 'No such hint'}, status=400)

    _, created = HintReveal.objects.get_or_create(
        user=user, objective=objective, level=level,
    )

    payload = _state(user)
    payload.update({
        'objective': objective,
        'level': level,
        'hint': text,
        'hintHtml': code_spans(text),
        'charged': created,
    })
    return JsonResponse(payload)


@xframe_options_sameorigin
def final_design(request):
    """Render the player's own submitted design: the markup + *their* CSS.

    Distinct from `final_preview`, which renders the official solution. This
    one is what the judges look at, and it only exists once the round has
    been submitted.
    """
    if not request.user.is_authenticated:
        return redirect('login')

    finalize_if_due(request.user)
    final = FinalSubmission.objects.filter(user=request.user).first()
    if not final:
        return redirect('home')

    return _render_novacloud(final.final_css)


@xframe_options_sameorigin
def final_preview(request):
    """Render the finished NovaCloud page: the fixed markup + the gold standard.

    A *visual reference only*. It reads nothing from the player and writes
    nothing back: no grading, no progress, no autosave, and deliberately no
    `start_challenge()` call, so opening it never touches the clock.

    The composed document is served from here rather than embedded in the arena
    page so the arena's own source never carries the answers, and it is loaded
    into a sandboxed iframe so it cannot reach the game shell.
    """
    if not request.user.is_authenticated:
        return redirect('login')

    return _render_novacloud(SOLUTION_CSS)


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

def _read_submitted_css(request):
    """The challenge is CSS only: any `html` field in the POST is ignored.

    The markup the player sees is read-only and the markup the checker grades
    always comes from `CHALLENGE_HTML`, so a forged `html` parameter cannot
    change the page or the score.
    """
    return (request.POST.get('css') or '')[:MAX_SUBMISSION_CHARS]


@require_POST
def save_css(request):
    """Autosave. Refuses writes once the session is locked (time up / done)."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not logged in'}, status=403)

    state = _state(request.user)
    if state['locked']:
        state['error'] = 'Time is up!' if state['expired'] else 'Challenge already completed.'
        return JsonResponse(state, status=200)

    css = _read_submitted_css(request)
    CssRule.objects.update_or_create(
        user=request.user, defaults={'css': css},
    )
    return JsonResponse(state)


def get_css(request):
    if not request.user.is_authenticated:
        return JsonResponse({'html': '', 'css': ''})
    submission = _get_submission(request.user)
    # The markup is the same for everybody and is never edited.
    return JsonResponse({'html': CHALLENGE_HTML, 'css': submission.css})


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
        payload['checks'] = run_checks(CHALLENGE_HTML, submission.css)
        payload['error'] = 'Time is up!' if user.is_expired else 'Challenge already completed.'
        return JsonResponse(payload)

    css = _read_submitted_css(request)
    CssRule.objects.update_or_create(user=user, defaults={'css': css})

    checks = run_checks(CHALLENGE_HTML, css)
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
        user=request.user, defaults={'css': STARTER_CSS},
    )
    payload = _state(request.user)
    payload.update({'css': STARTER_CSS})
    return JsonResponse(payload)

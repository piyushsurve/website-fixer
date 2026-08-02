from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout
from django.utils import timezone
from .models import CssRule, User

GAME_DURATION = 30  # seconds

# Index page
def index(request):
    return render(request, 'index.html')

def intro(request):
    return render(request, "intro.html")

# Signup view
@csrf_exempt
def user_signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        username = request.POST.get("username")
        pc_no = request.POST.get("pc_no")
        password = request.POST.get("password")

        if User.objects.filter(pc_no=pc_no).exists():
            return render(request, "signup.html", {"error": "PC number already registered"})

        user = User.objects.create_user(username=username, pc_no=pc_no, password=password)
        user.backend = "first.backends.PCNoBackend"
        # set start time immediately
        user.game_start_time = timezone.now()
        user.save()
        login(request, user)
        return redirect("home")

    return render(request, "signup.html")


# Login view
@csrf_exempt
def user_login(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        pc_no = request.POST.get("pc_no")
        password = request.POST.get("password")
        user = authenticate(request, pc_no=pc_no, password=password)
        if user:
            user.backend = "first.backends.PCNoBackend"
            login(request, user)

            # only set start time if not already set
            if not user.game_start_time:
                user.game_start_time = timezone.now()
                user.save()

            return redirect("home")
        else:
            return render(request, "login.html", {"error": "Invalid credentials"})
    return render(request, "login.html")


# Logout view
def user_logout(request):
    logout(request)
    return redirect("login")


# Helper: calculate remaining time
def get_remaining_time(user):
    if not user.game_start_time:
        return GAME_DURATION
    elapsed = (timezone.now() - user.game_start_time).total_seconds()
    return max(0, GAME_DURATION - int(elapsed))


# Home view
def home(request):
    if not request.user.is_authenticated:
        return redirect("login")

    css = ""
    rule = CssRule.objects.filter(user=request.user).last()
    if rule:
        css = rule.css

    remaining = get_remaining_time(request.user)

    return render(request, "entry.html", {"css": css, "remaining_time": remaining})


# Save CSS
@csrf_exempt
def save_css(request):
    if request.method == "POST" and request.user.is_authenticated:
        remaining = get_remaining_time(request.user)
        if remaining <= 0:
            return JsonResponse({"error": "Time is up!", "css": "", "remaining_time": 0})

        css = request.POST.get("css", "")
        CssRule.objects.update_or_create(user=request.user, defaults={"css": css})
        return JsonResponse({"css": css, "remaining_time": remaining})
    return JsonResponse({"error": "Not logged in"})


# Get CSS
def get_css(request):
    if request.user.is_authenticated:
        rule = CssRule.objects.filter(user=request.user).last()
        return JsonResponse({"css": rule.css if rule else ""})
    return JsonResponse({"css": ""})

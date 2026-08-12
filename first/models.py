from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.utils import timezone

from .game_config import GAME_DURATION_SECONDS


# Custom User Manager
class UserManager(BaseUserManager):
    def create_user(self, username, pc_no, password=None):
        if not username or not pc_no:
            raise ValueError("Users must have a username and PC number")
        user = self.model(username=username, pc_no=pc_no)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, pc_no, password=None):
        user = self.create_user(username=username, pc_no=pc_no, password=password)
        user.is_admin = True
        user.save(using=self._db)
        return user


# Custom User Model
class User(AbstractBaseUser):
    username = models.CharField(max_length=100)
    pc_no = models.CharField(max_length=50, unique=True)
    password = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)

    # The challenge clock. Set when the player first opens the arena and
    # never reset by a refresh -- this is the authoritative timer source.
    game_start_time = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    best_score = models.PositiveSmallIntegerField(default=0)

    objects = UserManager()

    USERNAME_FIELD = "pc_no"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.username

    # -- challenge clock ---------------------------------------------------

    def start_challenge(self):
        """Start the clock once. Refreshing the page must not restart it."""
        if not self.game_start_time:
            self.game_start_time = timezone.now()
            self.save(update_fields=["game_start_time"])

    @property
    def remaining_seconds(self):
        if not self.game_start_time:
            return GAME_DURATION_SECONDS
        elapsed = (timezone.now() - self.game_start_time).total_seconds()
        return max(0, int(GAME_DURATION_SECONDS - elapsed))

    @property
    def is_expired(self):
        return self.game_start_time is not None and self.remaining_seconds <= 0

    @property
    def is_completed(self):
        return self.completed_at is not None

    @property
    def is_locked(self):
        """No further edits accepted: either finished or out of time."""
        return self.is_completed or self.is_expired


# The player's saved submission (one row per player).
class CssRule(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="css_rules")
    html = models.TextField(blank=True, default="")
    css = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Submission for {self.user.username}"

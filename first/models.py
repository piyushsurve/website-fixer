from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.utils import timezone

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

    # NEW FIELD: when the game started
    game_start_time = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "pc_no"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.username


# CSS linked to each user
class CssRule(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="css_rules")
    css = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"CSS for {self.user.username}"

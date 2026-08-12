from django.contrib import admin

from .models import CssRule, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('pc_no', 'username', 'game_start_time', 'best_score', 'completed_at')
    search_fields = ('pc_no', 'username')


@admin.register(CssRule)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'updated_at')
    search_fields = ('user__pc_no', 'user__username')

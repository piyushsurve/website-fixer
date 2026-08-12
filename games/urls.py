from django.contrib import admin
from django.urls import path

from first import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Game home page
    path('', views.intro, name='intro'),

    # Auth routes
    path('signup/', views.user_signup, name='signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),

    # Game routes
    path('start/', views.start, name='start'),
    path('home/', views.home, name='home'),

    # Arena API
    path('save-css/', views.save_css, name='save_css'),
    path('get-css/', views.get_css, name='get_css'),
    path('api/state/', views.api_state, name='api_state'),
    path('api/check/', views.api_check, name='api_check'),
    path('api/reset/', views.api_reset, name='api_reset'),

    # Visual reference only: the finished page, never graded.
    path('api/final-preview/', views.final_preview, name='api_final_preview'),
]

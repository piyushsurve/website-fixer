from django.contrib import admin
from django.urls import path
from first import views  # adjust 'first' to your app name

urlpatterns = [
    path('admin/', admin.site.urls),

    # Intro page (default landing)
    path('', views.intro, name='intro'),

    # Auth routes
    path('signup/', views.user_signup, name='signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),

    # Game routes
    path('home/', views.home, name='home'),
    path('save-css/', views.save_css, name='save_css'),
    path('get-css/', views.get_css, name='get_css'),
]

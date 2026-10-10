from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

app_name = "core"
urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("access/<str:token>/", views.shared_access, name="shared_access"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("logout/", LogoutView.as_view(next_page="core:home"), name="logout"),
    path("health/", views.health, name="health"),
]

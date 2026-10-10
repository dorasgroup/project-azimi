from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.core.cache import caches
from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from .forms import UserProfileForm
from secrets import compare_digest


SHARED_ACCESS_USERNAME = "shared_link_operator"


def shared_access(request, token):
    configured_token = settings.SHARED_ACCESS_TOKEN
    if not configured_token or not compare_digest(token, configured_token):
        raise Http404

    user, _ = get_user_model().objects.get_or_create(
        username=SHARED_ACCESS_USERNAME,
        defaults={"password": make_password(None), "is_staff": True},
    )
    if not (user.is_active and user.is_staff and not user.is_superuser and not user.has_usable_password()):
        raise PermissionDenied
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("core:home")


class HomeView(LoginView):
    """The single human entry point: login for guests, console for staff."""

    template_name = "core/login.html"
    redirect_authenticated_user = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not request.user.is_staff:
                raise PermissionDenied
            from apps.monitoring.views import dashboard_context

            return render(request, "monitoring/dashboard.html", dashboard_context())
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("core:home")


class ProfileView(LoginRequiredMixin, View):
    """Allow logged-in staff to update their username and/or password."""

    template_name = "core/profile.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.username == SHARED_ACCESS_USERNAME:
            raise PermissionDenied
        if not request.user.is_staff:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        form = UserProfileForm(user=request.user)
        return render(request, self.template_name, {"form": form})

    def post(self, request, *args, **kwargs):
        form = UserProfileForm(request.POST, user=request.user)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "اطلاعات حساب کاربری شما با موفقیت بروزرسانی شد.")
            return redirect("core:profile")
        return render(request, self.template_name, {"form": form})


def _database_is_available() -> bool:
    try:
        connection.ensure_connection()
    except Exception:
        return False
    return True


def _redis_is_available() -> bool:
    try:
        caches["default"].get("health-check")
    except Exception:
        return False
    return True


def health(request):
    database_ok = _database_is_available()
    redis_ok = _redis_is_available()
    healthy = database_ok and redis_ok
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "services": {"web": "ok", "database": "ok" if database_ok else "unavailable", "redis": "ok" if redis_ok else "unavailable"}},
        status=200 if healthy else 503,
    )

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve


def serve_static(request, path):
    root = settings.STATIC_ROOT if (settings.STATIC_ROOT and settings.STATIC_ROOT.exists()) else settings.STATICFILES_DIRS[0]
    return serve(request, path, document_root=root)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("monitoring/", include("apps.monitoring.urls")),
    path("", include("apps.core.urls")),
    re_path(r"^static/(?P<path>.*)$", serve_static, name="static"),
]


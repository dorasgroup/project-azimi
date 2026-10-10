from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, RedirectView, TemplateView, UpdateView
from django.core.paginator import Paginator
import os
from datetime import timedelta
from django.conf import settings
from django.views import View
from django.shortcuts import get_object_or_404

from .forms import AutomationMonitoringForm, AutomationScheduleForm, BaleDestinationForm, KeywordForm, RecentDaysMonitoringForm, SourceForm, SourceGroupForm, TopicForm
from .models import AuditLog, AutomationRun, AutomationSchedule, AutomationState, BaleDestination, CollectionRun, ContentItem, Delivery, IdeaDelivery, Keyword, Source, SourceGroup, SystemSetting, Topic, TrendAnalysisRun, TrendCluster
from .services import bulk_delete_automation_runs, bulk_delete_content_items, bulk_delete_trend_clusters, delete_single_automation_run, delete_single_content_item, delete_single_trend_cluster, purge_content_items, record_audit, stop_automation_run
from .collection_services import create_manual_run, create_recent_days_run, delete_terminal_run, request_cancel
from .tasks import execute_collection_run
from .trend_services import create_analysis_run, refresh_keyword_matches
from .tasks import execute_trend_analysis
from .delivery_services import create_delivery, create_resend_delivery, eligible_clusters, sync_bale_destinations
from .bale_client import BaleClient, BaleClientError
from .tasks import execute_bale_delivery
from .automation_services import start_manual_pipeline, start_schedule_pipeline
from .tasks import execute_automation_pipeline
from .tasks import execute_idea_delivery


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return self.request.user.is_staff

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return redirect_to_login(self.request.get_full_path(), self.get_login_url(), self.get_redirect_field_name())
        return super().handle_no_permission()


def dashboard_context():
    setting = SystemSetting.get_solo()
    latest_automation = AutomationRun.objects.first()
    active_schedules=list(AutomationSchedule.objects.filter(is_active=True).prefetch_related("sources","topics"))
    next_run_at=min((schedule.next_run_at for schedule in active_schedules),default=None)
    active_statuses = (TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED)
    collection_runs = CollectionRun.objects.prefetch_related("source_results__source")
    in_progress_statuses = (AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING)
    completed_statuses = (
        AutomationRun.Status.SUCCESS,
        AutomationRun.Status.PARTIAL,
        AutomationRun.Status.FAILED,
        AutomationRun.Status.SKIPPED,
    )
    latest_collection_runs = collection_runs[:5]

    from django.utils import timezone
    now = timezone.now()
    twenty_four_hours_ago = now - timedelta(hours=24)
    thirty_days_ago = now - timedelta(days=30)

    new_content_24h = ContentItem.objects.filter(published_at__gte=twenty_four_hours_ago).count()

    total_runs_30d = CollectionRun.objects.filter(started_at__gte=thirty_days_ago).count() + AutomationRun.objects.filter(started_at__gte=thirty_days_ago).count()
    successful_runs_30d = CollectionRun.objects.filter(started_at__gte=thirty_days_ago, status=CollectionRun.Status.SUCCESS).count() + AutomationRun.objects.filter(started_at__gte=thirty_days_ago, status=AutomationRun.Status.SUCCESS).count()
    if total_runs_30d > 0:
        success_rate = int((successful_runs_30d / total_runs_30d) * 100)
    else:
        success_rate = 0

    total_completed = CollectionRun.objects.filter(status__in=[CollectionRun.Status.SUCCESS, CollectionRun.Status.PARTIAL]).count() + AutomationRun.objects.filter(status__in=completed_statuses).count()
    coll_in_progress = CollectionRun.objects.filter(status__in=[CollectionRun.Status.PENDING, CollectionRun.Status.RUNNING]).count()
    auto_in_progress = AutomationRun.objects.filter(status__in=in_progress_statuses).count()

    return {
        "monitoring_in_progress_count": auto_in_progress + coll_in_progress,
        "monitoring_completed_count": total_completed,
        "new_content_count_24h": new_content_24h,
        "success_rate": success_rate,
        "total_sources_count": Source.objects.filter(is_active=True).count(),
        "total_topics_count": Topic.objects.filter(is_active=True).count(),
        "active_trends": TrendCluster.objects.filter(status__in=active_statuses, merged_into__isnull=True).count(),
        "sensitive_trends": TrendCluster.objects.filter(status__in=active_statuses, is_sensitive=True, merged_into__isnull=True).count(),
        "delivered_count": Delivery.objects.filter(status=Delivery.Status.DELIVERED).count(),
        "latest_trends": TrendCluster.objects.filter(merged_into__isnull=True, status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED]).select_related("topic")[:4],
        "latest_run": collection_runs.first(),
        "latest_telegram_run": collection_runs.filter(source_results__source__platform=Source.Platform.TELEGRAM).distinct().first(),
        "latest_x_run": collection_runs.filter(source_results__source__platform=Source.Platform.X).distinct().first(),
        "latest_trend_run": TrendAnalysisRun.objects.first(),
        "latest_delivery": Delivery.objects.select_related("destination", "trend_cluster__topic").first(),
        "latest_collection_runs": latest_collection_runs[:3],
        "collection_runs_have_active": collection_runs.filter(status__in=(CollectionRun.Status.PENDING, CollectionRun.Status.RUNNING)).exists(),
        "automation_setting": setting,
        "active_automation_schedules": active_schedules,
        "active_automation_schedule_count": len(active_schedules),
        "latest_automation_run": latest_automation,
        "next_automation_run_at": next_run_at,
        "recent_days_form": RecentDaysMonitoringForm(),
        "automation_monitoring_form": AutomationMonitoringForm(auto_id="id_automation_%s"),
    }


class DashboardView(StaffRequiredMixin, RedirectView):
    pattern_name = "core:home"
    permanent = False


class AuditedFormMixin(StaffRequiredMixin):
    audit_operation = "update"

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, operation=self.audit_operation, instance=self.object)
        return response


class FilteredListView(StaffRequiredMixin, ListView):
    paginate_by = 20


class TopicListView(FilteredListView):
    model = Topic
    template_name = "monitoring/topic_list.html"

    def get_queryset(self):
        q = Topic.objects.prefetch_related("keywords").order_by("name")
        query = self.request.GET.get("q", "").strip()
        if query:
            q = q.filter(Q(name__icontains=query) | Q(keywords__phrase__icontains=query)).distinct()
        return q

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["total_topics_count"] = Topic.objects.count()
        context["total_keywords_count"] = Keyword.objects.count()
        return context


class TopicDetailView(StaffRequiredMixin, View):
    template_name = "monitoring/topic_detail.html"

    def get(self, request, pk):
        topic = get_object_or_404(Topic.objects.prefetch_related("keywords"), pk=pk)
        keywords = topic.keywords.all().order_by("-is_active", "phrase")
        query = request.GET.get("q", "").strip()
        if query:
            keywords = keywords.filter(phrase__icontains=query)
        form = KeywordForm(initial={"topic": topic, "weight": 10, "is_active": True, "match_type": Keyword.MatchType.CONTAINS})
        return render(request, self.template_name, {
            "topic": topic,
            "keywords": keywords,
            "form": form,
        })

    def post(self, request, pk):
        topic = get_object_or_404(Topic, pk=pk)
        data = request.POST.copy()
        data["topic"] = str(topic.pk)
        form = KeywordForm(data)
        if form.is_valid():
            keyword = form.save(commit=False)
            keyword.topic = topic
            keyword.save()
            refresh_keyword_matches(ContentItem.objects.all())
            record_audit(actor=request.user, operation="create", instance=keyword)
            messages.success(request, f"کلیدواژه «{keyword.phrase}» با موفقیت به موضوع «{topic.name}» اضافه شد.")
            return redirect("monitoring:topic_detail", pk=topic.pk)
        keywords = topic.keywords.all().order_by("-is_active", "phrase")
        return render(request, self.template_name, {
            "topic": topic,
            "keywords": keywords,
            "form": form,
        })


class TopicCreateView(AuditedFormMixin, CreateView):
    model = Topic
    form_class = TopicForm
    template_name = "monitoring/form.html"
    audit_operation = "create"

    def get_success_url(self):
        messages.success(self.request, f"موضوع «{self.object.name}» با موفقیت ساخته شد. اکنون می‌توانید کلیدواژه‌های این موضوع را در این بخش اضافه کنید.")
        return reverse("monitoring:topic_detail", kwargs={"pk": self.object.pk})


class TopicUpdateView(AuditedFormMixin, UpdateView):
    model, form_class, template_name = Topic, TopicForm, "monitoring/form.html"

    def get_success_url(self):
        return reverse("monitoring:topic_detail", kwargs={"pk": self.object.pk})


class ProtectedDeleteView(StaffRequiredMixin, DeleteView):
    template_name = "monitoring/confirm_delete.html"

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, "این مورد به داده‌های وابسته متصل است و حذف آن ممکن نیست.")
            return redirect(self.success_url)


class TopicDeleteView(ProtectedDeleteView):
    model, success_url = Topic, reverse_lazy("monitoring:topic_list")


class KeywordListView(StaffRequiredMixin, RedirectView):
    """Keep the historical keyword list URL, with one combined configuration UI."""

    pattern_name = "monitoring:topic_list"
    query_string = True


class KeywordCreateView(AuditedFormMixin, CreateView):
    model, form_class, template_name, audit_operation = Keyword, KeywordForm, "monitoring/form.html", "create"

    def get_initial(self):
        initial = super().get_initial()
        topic_id = self.request.GET.get("topic")
        if topic_id:
            initial["topic"] = topic_id
        return initial

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url:
            return next_url
        if self.object and self.object.topic_id:
            return reverse("monitoring:topic_detail", kwargs={"pk": self.object.topic_id})
        return reverse("monitoring:topic_list")


class KeywordUpdateView(AuditedFormMixin, UpdateView):
    model, form_class, template_name = Keyword, KeywordForm, "monitoring/form.html"

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url:
            return next_url
        if self.object and self.object.topic_id:
            return reverse("monitoring:topic_detail", kwargs={"pk": self.object.topic_id})
        return reverse("monitoring:topic_list")


class KeywordDeleteView(ProtectedDeleteView):
    model = Keyword

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url:
            return next_url
        if self.object and self.object.topic_id:
            return reverse("monitoring:topic_detail", kwargs={"pk": self.object.topic_id})
        return reverse("monitoring:topic_list")


class SourceGroupListView(FilteredListView):
    model = SourceGroup
    template_name = "monitoring/source_group_list.html"


class SourceGroupCreateView(AuditedFormMixin, CreateView):
    model, form_class, template_name, success_url, audit_operation = SourceGroup, SourceGroupForm, "monitoring/form.html", reverse_lazy("monitoring:source_group_list"), "create"


class SourceGroupUpdateView(AuditedFormMixin, UpdateView):
    model, form_class, template_name, success_url = SourceGroup, SourceGroupForm, "monitoring/form.html", reverse_lazy("monitoring:source_group_list")


class SourceGroupDeleteView(ProtectedDeleteView):
    model, success_url = SourceGroup, reverse_lazy("monitoring:source_group_list")


class SourceListView(FilteredListView):
    model = Source
    template_name = "monitoring/source_list.html"
    paginate_by = None

    def get_queryset(self):
        queryset = Source.objects.all()
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(display_name__icontains=query) | Q(discovered_name__icontains=query) | Q(identifier__icontains=query))
        for field in ("platform", "importance", "is_active"):
            value = self.request.GET.get(field)
            if value not in (None, ""):
                queryset = queryset.filter(**{field: value})
        return queryset.order_by("platform", "display_name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["general_sources"] = context["object_list"].filter(importance=Source.Importance.GENERAL)
        context["special_sources"] = context["object_list"].filter(importance=Source.Importance.SPECIAL)
        return context


class SourceCreateView(AuditedFormMixin, CreateView):
    model, form_class, template_name, success_url, audit_operation = Source, SourceForm, "monitoring/form.html", reverse_lazy("monitoring:source_list"), "create"


class SourceUpdateView(AuditedFormMixin, UpdateView):
    model, form_class, template_name, success_url = Source, SourceForm, "monitoring/form.html", reverse_lazy("monitoring:source_list")


class SourceDeleteView(ProtectedDeleteView):
    model, success_url = Source, reverse_lazy("monitoring:source_list")


class AutomationRunListView(FilteredListView):
    model = AutomationRun
    template_name = "monitoring/automation_run_list.html"

    def get_queryset(self):
        q = AutomationRun.objects.select_related("schedule", "telegram_run", "x_run", "trend_analysis_run")
        if self.request.GET.get("status"):
            q = q.filter(status=self.request.GET["status"])
        if self.request.GET.get("date_from"):
            q = q.filter(started_at__date__gte=self.request.GET["date_from"])
        if self.request.GET.get("date_to"):
            q = q.filter(started_at__date__lte=self.request.GET["date_to"])
        return q

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["automation_schedules"] = AutomationSchedule.objects.prefetch_related("sources","topics")
        context["active_run"] = AutomationRun.objects.filter(
            status__in=(AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING)
        ).first()
        return context



class CollectionRunListView(FilteredListView):
    model = CollectionRun
    template_name = "monitoring/run_list.html"

    def get_queryset(self):
        queryset = CollectionRun.objects.prefetch_related("source_results__source").exclude(
            telegram_automation_runs__isnull=False
        ).exclude(x_automation_runs__isnull=False)
        for field in ("status", "run_type"):
            value = self.request.GET.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        if self.request.GET.get("date_from"):
            queryset = queryset.filter(started_at__date__gte=self.request.GET["date_from"])
        if self.request.GET.get("date_to"):
            queryset = queryset.filter(started_at__date__lte=self.request.GET["date_to"])
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["recent_days_form"] = RecentDaysMonitoringForm()
        return context


class ContentItemListView(FilteredListView):
    model = ContentItem
    template_name = "monitoring/post_list.html"

    def get_queryset(self):
        queryset = ContentItem.objects.select_related("source").prefetch_related(
            "keyword_matches__topic",
            "keyword_matches__keyword",
            "cluster_memberships__cluster",
        )
        query = self.request.GET.get("q", "").strip()
        if query: queryset = queryset.filter(normalized_text__icontains=query)
        if self.request.GET.get("platform"): queryset = queryset.filter(source__platform=self.request.GET["platform"])
        if self.request.GET.get("source"): queryset = queryset.filter(source_id=self.request.GET["source"])
        if self.request.GET.get("topic"):
            queryset = queryset.filter(keyword_matches__topic_id=self.request.GET["topic"]).distinct()
        if self.request.GET.get("trends_only") == "1":
            queryset = queryset.filter(
                cluster_memberships__cluster__merged_into__isnull=True,
                cluster_memberships__cluster__status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
            ).distinct()
        if self.request.GET.get("date_from"): queryset = queryset.filter(published_at__date__gte=self.request.GET["date_from"])
        if self.request.GET.get("date_to"): queryset = queryset.filter(published_at__date__lte=self.request.GET["date_to"])
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sources"] = Source.objects.all()
        context["topics"] = Topic.objects.filter(is_active=True)
        context["total_count"] = ContentItem.objects.count()
        context["trends_only"] = self.request.GET.get("trends_only") == "1"
        context["shared_items_count"] = ContentItem.objects.filter(
            cluster_memberships__cluster__merged_into__isnull=True,
            cluster_memberships__cluster__status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
        ).distinct().count()
        return context


class ContentItemDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if delete_single_content_item(pk, actor=request.user):
            messages.success(request, "محتوا با موفقیت حذف شد.")
        else:
            messages.error(request, "محتوای مورد نظر یافت نشد.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:post_list")


class ContentItemBulkDeleteView(StaffRequiredMixin, View):
    def post(self, request):
        ids = request.POST.getlist("selected_ids")
        if not ids:
            messages.warning(request, "هیچ محتوایی برای حذف انتخاب نشده است.")
        else:
            count = bulk_delete_content_items(ids, actor=request.user)
            messages.success(request, f"{count} محتوا با موفقیت حذف شد.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:post_list")


class ContentItemPurgeView(StaffRequiredMixin, View):
    def post(self, request):
        query = request.POST.get("q", "").strip() or None
        platform = request.POST.get("platform", "").strip() or None
        source_id = request.POST.get("source", "").strip() or None
        date_from = request.POST.get("date_from", "").strip() or None
        date_to = request.POST.get("date_to", "").strip() or None
        count = purge_content_items(
            actor=request.user,
            query=query,
            platform=platform,
            source_id=source_id,
            date_from=date_from,
            date_to=date_to,
        )
        if count > 0:
            messages.success(request, f"تمام محتواهای مشخص‌شده ({count} مورد) با موفقیت پاکسازی شدند.")
        else:
            messages.info(request, "محتوایی برای پاکسازی یافت نشد.")
        return redirect("monitoring:post_list")



class CollectionRunDetailView(StaffRequiredMixin, TemplateView):
    template_name = "monitoring/run_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = get_object_or_404(CollectionRun, pk=self.kwargs["pk"])
        context["run"] = run
        if run.selected_topics.exists():
            trends = TrendCluster.objects.filter(
                topic__in=run.selected_topics.all(),
                merged_into__isnull=True,
                status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
            ).prefetch_related("items__content_item__source", "topic", "representative").order_by("-score", "-last_published_at")
            if run.requested_start_at:
                trends = trends.filter(last_published_at__gte=run.requested_start_at)
            context["run_trends"] = trends

        source_ids = list(run.source_results.values_list("source_id", flat=True))
        posts = ContentItem.objects.filter(source_id__in=source_ids).select_related("source").prefetch_related(
            "keyword_matches__topic",
            "keyword_matches__keyword",
            "cluster_memberships__cluster",
        )
        if run.requested_start_at:
            posts = posts.filter(published_at__gte=run.requested_start_at)
            if run.requested_end_at:
                posts = posts.filter(published_at__lte=run.requested_end_at)
        elif run.started_at:
            time_filtered = posts.filter(published_at__gte=run.started_at - timedelta(hours=2))
            if run.finished_at:
                time_filtered = time_filtered.filter(published_at__lte=run.finished_at + timedelta(hours=2))
            if time_filtered.exists():
                posts = time_filtered

        if run.selected_topics.exists():
            posts = posts.filter(keyword_matches__topic__in=run.selected_topics.all()).distinct()

        query = self.request.GET.get("q", "").strip()
        if query:
            posts = posts.filter(normalized_text__icontains=query)
        if self.request.GET.get("platform"):
            posts = posts.filter(source__platform=self.request.GET["platform"])
        if self.request.GET.get("source"):
            posts = posts.filter(source_id=self.request.GET["source"])
        if self.request.GET.get("trends_only") == "1":
            posts = posts.filter(
                cluster_memberships__cluster__merged_into__isnull=True,
                cluster_memberships__cluster__status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
            ).distinct()

        posts = posts.order_by("-published_at", "-id")

        paginator = Paginator(posts, 25)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["posts"] = page_obj
        context["page_obj"] = page_obj
        context["paginator"] = paginator
        context["is_paginated"] = page_obj.has_other_pages()
        context["total_run_posts_count"] = posts.count()
        context["run_sources"] = Source.objects.filter(id__in=source_ids).order_by("display_name", "identifier")
        return context


class LatestCollectionRunsPartialView(StaffRequiredMixin, TemplateView):
    template_name = "monitoring/partials/latest_collection_runs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["latest_collection_runs"] = CollectionRun.objects.prefetch_related("source_results__source")[:5]
        context["collection_runs_have_active"] = CollectionRun.objects.filter(
            status__in=(CollectionRun.Status.PENDING, CollectionRun.Status.RUNNING)
        ).exists()
        return context


class CollectionRunCancelView(StaffRequiredMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(CollectionRun, pk=pk)
        force = request.POST.get("force") in ("true", "1") or bool(run.cancel_requested_at)
        run, changed = request_cancel(run, actor=request.user, force=force)
        if run.status == CollectionRun.Status.CANCELLED:
            messages.success(request, "رصد با موفقیت لغو شد.")
        elif changed:
            messages.info(request, "درخواست لغو ثبت شد؛ پس از اتمام منبع جاری متوقف می‌شود.")
        else:
            messages.info(request, "این رصد پیش‌تر پایان یافته است.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:run_detail", pk=run.pk)


class CollectionRunDeleteView(StaffRequiredMixin, View):
    template_name = "monitoring/run_delete_confirm.html"

    def get(self, request, pk):
        run = CollectionRun.objects.filter(pk=pk).first()
        if not run:
            messages.info(request, "این رصد پیش‌تر حذف شده است یا وجود ندارد.")
            return redirect("monitoring:run_list")
        opened_from_list = request.GET.get("from") == "list"
        return render(request, self.template_name, {
            "run": run,
            "base_template": "monitoring/run_list.html" if opened_from_list else "monitoring/run_detail.html",
            "delete_return_url": reverse("monitoring:run_list") if opened_from_list else reverse("monitoring:run_detail", kwargs={"pk": run.pk}),
            "opened_from_list": opened_from_list,
        })

    def post(self, request, pk):
        run = CollectionRun.objects.filter(pk=pk).first()
        if not run:
            messages.info(request, "این رصد پیش‌تر حذف شده است یا وجود ندارد.")
            return redirect("monitoring:run_list")
        if delete_terminal_run(run, actor=request.user):
            messages.success(request, "رکورد رصد و نتایج منبع آن حذف شد؛ محتواهای جمع‌آوری‌شده حفظ شدند.")
            return redirect("monitoring:run_list")
        messages.error(request, "حذف رصد در انتظار یا در حال اجرا مجاز نیست.")
        if request.POST.get("from") == "list":
            return redirect("monitoring:run_list")
        return redirect("monitoring:run_detail", pk=run.pk)


class StartTelegramRunView(StaffRequiredMixin, View):
    def post(self, request):
        run = create_manual_run()
        execute_collection_run.delay(run.pk)
        return redirect("monitoring:run_detail", pk=run.pk)


class StartXRunView(StaffRequiredMixin, View):
    def post(self, request):
        run = create_manual_run(Source.Platform.X)
        execute_collection_run.delay(run.pk)
        return redirect("monitoring:run_detail", pk=run.pk)


class StartRecentDaysMonitoringView(StaffRequiredMixin, View):
    def post(self, request):
        form = RecentDaysMonitoringForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("core:home")
        run = create_recent_days_run(sources=form.cleaned_data["sources"], days=form.cleaned_data["days"], topics=form.cleaned_data["topics"])
        record_audit(actor=request.user, operation="recent_days_monitoring", instance=run, metadata={"days": form.cleaned_data["days"], "source_count": run.target_sources_count, "topic_count": form.cleaned_data["topics"].count()})
        execute_collection_run.delay(run.pk)
        return redirect("monitoring:run_detail", pk=run.pk)


class SaveAutomationMonitoringView(StaffRequiredMixin, View):
    def post(self, request):
        form = AutomationMonitoringForm(request.POST)
        next_url = request.POST.get("next") or reverse("core:home")
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect(next_url)
        schedule=AutomationSchedule.objects.create(
            lookback_hours=form.cleaned_data["hours"],
            interval_hours=form.cleaned_data["interval_hours"],
            is_active=True,
        )
        schedule.sources.set(form.cleaned_data["sources"])
        schedule.topics.set(form.cleaned_data["topics"])
        run=start_schedule_pipeline(schedule)
        record_audit(
            actor=request.user,
            operation="automation_schedule_create",
            instance=schedule,
            metadata={
                "source_count": schedule.sources.count(),
                "topic_count": schedule.topics.count(),
                "lookback_hours": schedule.lookback_hours,
                "interval_hours": schedule.interval_hours,
            },
        )
        if run and run.status==AutomationRun.Status.PENDING:
            execute_automation_pipeline.delay(run.pk)
            messages.success(request,f"رصد خودکار #{schedule.pk} ثبت شد و اولین اجرا آغاز شد.")
            return redirect("monitoring:automation_run_detail",pk=run.pk)
        messages.success(request,f"رصد خودکار #{schedule.pk} ثبت شد و در نخستین فرصت اجرا می‌شود.")
        return redirect(next_url)


class AutomationScheduleUpdateView(AuditedFormMixin,UpdateView):
    model=AutomationSchedule
    form_class=AutomationScheduleForm
    template_name="monitoring/form.html"
    success_url=reverse_lazy("monitoring:automation_run_list")
    audit_operation="automation_schedule_update"


class AutomationScheduleToggleView(StaffRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(AutomationSchedule, pk=pk)
        schedule.is_active = not schedule.is_active
        schedule.save(update_fields=["is_active", "updated_at"])
        record_audit(
            actor=request.user,
            operation="automation_schedule_toggle",
            instance=schedule,
            metadata={"is_active": schedule.is_active},
        )
        messages.success(request, f"رصد خودکار #{schedule.pk} {'فعال' if schedule.is_active else 'غیرفعال'} شد.")
        return redirect("monitoring:automation_run_list")


class AutomationScheduleDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(AutomationSchedule, pk=pk)
        schedule_id = schedule.pk
        record_audit(
            actor=request.user,
            operation="automation_schedule_delete",
            instance=schedule,
            metadata={"interval_hours": schedule.interval_hours},
        )
        schedule.delete()
        messages.success(request, f"رصد خودکار #{schedule_id} حذف شد؛ تاریخچه اجراها حفظ شده است.")
        return redirect("monitoring:automation_run_list")


class TrendRunListView(StaffRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse("monitoring:trend_cluster_list")


class TrendRunDetailView(StaffRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse("monitoring:trend_cluster_list")


class TrendClusterListView(RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse("monitoring:dashboard")

    model=TrendCluster;template_name="monitoring/trend_cluster_list.html"
    def get_queryset(self):
        q = (
            TrendCluster.objects.select_related('topic', 'representative')
            .prefetch_related('items__content_item__source')
            .filter(merged_into__isnull=True, status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED])
        )
        for field in ('topic','status','is_sensitive'):
            if self.request.GET.get(field) not in (None,''): q=q.filter(**{field:self.request.GET[field]})
        if self.request.GET.get('platform'): q=q.filter(platforms__contains=[self.request.GET['platform']])
        if self.request.GET.get('min_score'): q=q.filter(score__gte=self.request.GET['min_score'])
        return q.order_by("-score", "-last_published_at")
    def get_context_data(self,**kwargs):
        c=super().get_context_data(**kwargs)
        c['topics']=Topic.objects.all()
        return c
class TrendClusterDetailView(StaffRequiredMixin,TemplateView):
    template_name="monitoring/trend_cluster_detail.html"
    def get_context_data(self,**kwargs):
        c=super().get_context_data(**kwargs);c['cluster']=get_object_or_404(TrendCluster.objects.select_related('merged_into').prefetch_related('merged_clusters','items__content_item__source'),pk=self.kwargs['pk']);c['bale_destinations']=BaleDestination.objects.filter(is_active=True);return c
class StartTrendAnalysisView(StaffRequiredMixin,View):
    def post(self,request):
        run=create_analysis_run();execute_trend_analysis.delay(run.pk);messages.success(request,"تحلیل ترندها آغاز شد و نتایج در این صفحه به‌روزرسانی می‌شوند.");return redirect('monitoring:trend_cluster_list')
class TrendClusterStatusView(StaffRequiredMixin,View):
    def post(self,request,pk,status):
        if status not in {TrendCluster.Status.CONFIRMED,TrendCluster.Status.DISMISSED,TrendCluster.Status.SUPPRESSED,TrendCluster.Status.CANDIDATE,TrendCluster.Status.TRENDING}:
            return redirect('monitoring:trend_cluster_detail',pk=pk)
        cluster=get_object_or_404(TrendCluster,pk=pk);cluster.status=status;cluster.save(update_fields=['status']);record_audit(actor=request.user,operation='trend_status',instance=cluster)
        if status in {TrendCluster.Status.CONFIRMED, TrendCluster.Status.TRENDING}:
            try:
                from .delivery_services import dispatch_bale_deliveries
                dispatch_bale_deliveries()
            except Exception:
                pass
        return redirect('monitoring:trend_cluster_detail',pk=pk)


class TrendClusterDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if delete_single_trend_cluster(pk, actor=request.user):
            messages.success(request, "ترند با موفقیت حذف شد.")
        else:
            messages.error(request, "ترند مورد نظر یافت نشد.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:trend_cluster_list")


class TrendClusterBulkDeleteView(StaffRequiredMixin, View):
    def post(self, request):
        ids = request.POST.getlist("selected_ids")
        if not ids:
            messages.warning(request, "هیچ ترندی برای حذف انتخاب نشده است.")
        else:
            count = bulk_delete_trend_clusters(ids, actor=request.user)
            messages.success(request, f"{count} ترند با موفقیت حذف شد.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:trend_cluster_list")



class BaleDestinationListView(FilteredListView):
    model=BaleDestination;template_name="monitoring/bale_destination_list.html"


class BaleDestinationCreateView(AuditedFormMixin,CreateView):
    model=BaleDestination;form_class=BaleDestinationForm;template_name="monitoring/form.html";success_url=reverse_lazy("monitoring:bale_destination_list");audit_operation="bale_destination_create"


class BaleDestinationUpdateView(AuditedFormMixin,UpdateView):
    model=BaleDestination;form_class=BaleDestinationForm;template_name="monitoring/form.html";success_url=reverse_lazy("monitoring:bale_destination_list");audit_operation="bale_destination_update"


class BaleDestinationDeleteView(ProtectedDeleteView):
    model=BaleDestination;success_url=reverse_lazy("monitoring:bale_destination_list")


def _update_env_bale_token(token: str):
    env_path = settings.BASE_DIR / ".env"
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("BALE_BOT_TOKEN="):
                new_lines.append(f"BALE_BOT_TOKEN={token}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"BALE_BOT_TOKEN={token}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ["BALE_BOT_TOKEN"] = token
    settings.BALE_BOT_TOKEN = token


class DeliveryListView(FilteredListView):
    model = Delivery
    template_name = "monitoring/delivery_list.html"

    def get_queryset(self):
        q = Delivery.objects.select_related("destination", "trend_cluster__topic", "trend_cluster__representative")
        for field in ("destination", "status"):
            if self.request.GET.get(field):
                q = q.filter(**{f"{field}_id" if field == "destination" else field: self.request.GET[field]})
        if self.request.GET.get("date_from"):
            q = q.filter(created_at__date__gte=self.request.GET["date_from"])
        if self.request.GET.get("date_to"):
            q = q.filter(created_at__date__lte=self.request.GET["date_to"])
        return q.order_by("-created_at")

    def get_context_data(self, **kwargs):
        c = super().get_context_data(**kwargs)
        all_deliveries = Delivery.objects.all()
        c["total_deliveries_count"] = all_deliveries.count()
        c["success_deliveries_count"] = all_deliveries.filter(status=Delivery.Status.DELIVERED).count()
        c["failed_deliveries_count"] = all_deliveries.filter(status=Delivery.Status.FAILED).count()
        c["pending_deliveries_count"] = all_deliveries.filter(status__in=[Delivery.Status.PENDING, Delivery.Status.SENDING]).count()

        token = getattr(settings, "BALE_BOT_TOKEN", "") or os.environ.get("BALE_BOT_TOKEN", "")
        c["raw_token"] = token
        if token:
            c["current_token_masked"] = f"{token[:8]}...{token[-5:]}" if len(token) > 15 else token
        else:
            c["current_token_masked"] = ""

        bot_info = self.request.session.get("bale_bot_info")
        if not bot_info and token and not getattr(settings, "TESTING", False):
            try:
                me = BaleClient(token=token).get_me()
                res = me.get("result") or {}
                bot_info = {
                    "connected": True,
                    "username": res.get("username", ""),
                    "first_name": res.get("first_name", ""),
                    "id": str(res.get("id", "")),
                }
                self.request.session["bale_bot_info"] = bot_info
            except Exception as exc:
                bot_info = {"connected": False, "error": str(exc)}
                self.request.session["bale_bot_info"] = bot_info
        c["bot_info"] = bot_info

        if token and not getattr(settings, "TESTING", False):
            try:
                sync_bale_destinations(client=BaleClient(token=token))
            except Exception:
                pass

        c["destinations"] = BaleDestination.objects.all()
        return c


class IdeaDeliveryListView(FilteredListView):
    model = IdeaDelivery
    template_name = "monitoring/idea_delivery_list.html"

    def get_queryset(self):
        queryset = IdeaDelivery.objects.select_related("trend_cluster", "trend_cluster__topic")
        for field in ("idea_status", "dify_status"):
            value = self.request.GET.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(topic_name__icontains=query)
                | Q(source_trend_id__icontains=query)
            )
        return queryset.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deliveries = IdeaDelivery.objects.all()
        context["integration_enabled"] = settings.IDEA_INTEGRATION_ENABLED
        context["total_count"] = deliveries.count()
        context["accepted_count"] = deliveries.filter(
            idea_status=IdeaDelivery.IdeaStatus.ACCEPTED
        ).count()
        context["indexed_count"] = deliveries.filter(
            dify_status=IdeaDelivery.DifyStatus.INDEXED
        ).count()
        context["failed_count"] = deliveries.filter(
            Q(idea_status=IdeaDelivery.IdeaStatus.FAILED)
            | Q(dify_status=IdeaDelivery.DifyStatus.FAILED)
        ).count()
        return context


class RetryIdeaDeliveryView(StaffRequiredMixin, View):
    def post(self, request, pk):
        delivery = get_object_or_404(IdeaDelivery, pk=pk)
        if delivery.idea_status == IdeaDelivery.IdeaStatus.FAILED:
            delivery.idea_status = IdeaDelivery.IdeaStatus.PENDING
            delivery.next_attempt_at = timezone.now()
            delivery.error_summary = ""
            delivery.save(
                update_fields=[
                    "idea_status",
                    "next_attempt_at",
                    "error_summary",
                    "updated_at",
                ]
            )
            record_audit(
                actor=request.user,
                operation="idea_delivery_retry",
                instance=delivery,
            )
            if settings.IDEA_INTEGRATION_ENABLED and settings.IDEA_API_TOKEN:
                execute_idea_delivery.delay(delivery.pk)
                messages.success(request, "ارسال دوباره در صف قرار گرفت.")
            else:
                messages.info(request, "رکورد آماده ارسال شد؛ اتصال هنوز غیرفعال است.")
        return redirect("monitoring:idea_delivery_list")


class DeliveryDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        delivery = get_object_or_404(Delivery, pk=pk)
        if delivery.status in {Delivery.Status.PENDING, Delivery.Status.SENDING}:
            messages.error(request, "حذف ارسالی که در صف یا در حال ارسال است مجاز نیست.")
        else:
            record_audit(actor=request.user, operation="bale_delivery_delete", instance=delivery)
            delivery.delete()
            messages.success(request, "رکورد ارسال بله حذف شد.")
        return redirect("monitoring:delivery_list")


class DeliveryBulkDeleteView(StaffRequiredMixin, View):
    def post(self, request):
        delivery_ids = request.POST.getlist("selected_ids")
        deliveries = Delivery.objects.filter(pk__in=delivery_ids).exclude(
            status__in=[Delivery.Status.PENDING, Delivery.Status.SENDING]
        )
        count = deliveries.count()
        for delivery in deliveries:
            record_audit(actor=request.user, operation="bale_delivery_delete", instance=delivery)
        deliveries.delete()
        if count:
            messages.success(request, f"{count} رکورد ارسال بله حذف شد.")
        else:
            messages.info(request, "ارسال قابل حذفی انتخاب نشده است.")
        return redirect("monitoring:delivery_list")


class BaleTokenUpdateView(StaffRequiredMixin, View):
    def post(self, request):
        token = request.POST.get("token", "").strip()
        if not token:
            messages.error(request, "لطفاً توکن معتبر بازوی بله را وارد کنید.")
            return redirect("monitoring:delivery_list")

        try:
            client = BaleClient(token=token)
            me = client.get_me()
            res = me.get("result") or {}
            username = res.get("username", "")
            first_name = res.get("first_name", "")
            bot_id = str(res.get("id", ""))
        except BaleClientError as exc:
            messages.error(request, f"خطا در اعتبارسنجی توکن بله: {exc}")
            return redirect("monitoring:delivery_list")
        except Exception as exc:
            messages.error(request, f"خطای ارتباطی در بررسی توکن بله: {exc}")
            return redirect("monitoring:delivery_list")

        _update_env_bale_token(token)
        request.session["bale_bot_info"] = {
            "connected": True,
            "username": username,
            "first_name": first_name,
            "id": bot_id,
        }
        AuditLog.objects.create(
            actor=request.user,
            operation="bale_token_update",
            entity_type="BaleBot",
            entity_id="bot_token",
            metadata={"bot_username": username, "bot_id": bot_id},
        )
        messages.success(request, f"توکن ربات بله با موفقیت ذخیره شد. متصل به بازوی @{username} ({first_name}).")
        return redirect("monitoring:delivery_list")


class BaleBotCheckView(StaffRequiredMixin, View):
    def post(self, request):
        token = getattr(settings, "BALE_BOT_TOKEN", "") or os.environ.get("BALE_BOT_TOKEN", "")
        if not token:
            messages.error(request, "توکن ربات بله هنوز تنظیم نشده است.")
            return redirect("monitoring:delivery_list")

        try:
            client = BaleClient(token=token)
            me = client.get_me()
            res = me.get("result") or {}
            username = res.get("username", "")
            first_name = res.get("first_name", "")
            bot_id = str(res.get("id", ""))
            request.session["bale_bot_info"] = {
                "connected": True,
                "username": username,
                "first_name": first_name,
                "id": bot_id,
            }
            synced = sync_bale_destinations(client=client)
            messages.success(request, f"اتصال به ربات بله برقرار است: @{username} ({first_name} - شناسه {bot_id}). {len(synced)} کانال/گروه مقصد شناسایی و فعال شدند.")
        except BaleClientError as exc:
            request.session["bale_bot_info"] = {"connected": False, "error": str(exc)}
            messages.error(request, f"خطا در اتصال به ربات بله: {exc}")
        except Exception as exc:
            request.session["bale_bot_info"] = {"connected": False, "error": str(exc)}
            messages.error(request, f"خطای ارتباط با سرور بله: {exc}")

        return redirect("monitoring:delivery_list")



class DeliveryDetailView(StaffRequiredMixin,TemplateView):
    template_name="monitoring/delivery_detail.html"
    def get_context_data(self,**kwargs):
        c=super().get_context_data(**kwargs);c["delivery"]=get_object_or_404(Delivery.objects.select_related("destination","trend_cluster__topic"),pk=self.kwargs["pk"]);return c


class SendTrendDeliveryView(StaffRequiredMixin,View):
    def post(self,request,pk):
        cluster=get_object_or_404(TrendCluster,pk=pk);destination=get_object_or_404(BaleDestination,is_active=True,pk=request.POST.get("destination"))
        try:
            delivery,created=create_delivery(destination=destination,cluster=cluster)
        except ValueError as exc:
            if Delivery.objects.filter(destination=destination,trend_cluster=cluster,status=Delivery.Status.DELIVERED).exists():
                delivery=create_resend_delivery(destination=destination,cluster=cluster)
                created=True
            else:
                messages.error(request,str(exc));return redirect("monitoring:trend_cluster_detail",pk=pk)
        if not created and delivery.status==Delivery.Status.DELIVERED:
            delivery=create_resend_delivery(destination=destination,cluster=cluster)
            created=True
        record_audit(actor=request.user,operation="bale_delivery_create",instance=delivery)
        if created or delivery.status!=Delivery.Status.DELIVERED: execute_bale_delivery.delay(delivery.pk)
        return redirect("monitoring:delivery_detail",pk=delivery.pk)


class RunBaleDispatchView(StaffRequiredMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(CollectionRun, pk=pk)
        topics = run.selected_topics.filter(is_active=True)
        if not topics.exists():
            topics = Topic.objects.filter(is_active=True)
        run_trends = TrendCluster.objects.filter(
            topic__in=topics,
            merged_into__isnull=True,
            status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
        )
        if run.requested_start_at:
            run_trends = run_trends.filter(last_published_at__gte=run.requested_start_at)
        from .delivery_services import dispatch_bale_deliveries
        delivery_ids = dispatch_bale_deliveries(clusters=run_trends, force_resend=True)
        record_audit(actor=request.user, operation="bale_run_dispatch", instance=run, metadata={"deliveries_count": len(delivery_ids)})
        if delivery_ids:
            messages.success(request, f"{len(delivery_ids)} گزارش ترند با موفقیت در صف ارسال به مقاصد فعال بله قرار گرفت.")
        else:
            messages.info(request, "ترند واجد شرایطی برای ارسال یافت نشد.")
        return redirect("monitoring:run_detail", pk=run.pk)


class RetryDeliveryView(StaffRequiredMixin,View):
    def post(self,request,pk):
        delivery=get_object_or_404(Delivery,pk=pk)
        if delivery.status==Delivery.Status.FAILED:
            record_audit(actor=request.user,operation="bale_delivery_retry",instance=delivery);execute_bale_delivery.delay(delivery.pk)
        return redirect("monitoring:delivery_detail",pk=pk)


class ResendDeliveryView(StaffRequiredMixin,View):
    def post(self,request,pk):
        old=get_object_or_404(Delivery,pk=pk)
        if old.status==Delivery.Status.DELIVERED:
            delivery=create_resend_delivery(destination=old.destination,cluster=old.trend_cluster)
            record_audit(actor=request.user,operation="bale_delivery_resend",instance=delivery);execute_bale_delivery.delay(delivery.pk)
            return redirect("monitoring:delivery_detail",pk=delivery.pk)
        return redirect("monitoring:delivery_detail",pk=pk)


class BaleConnectionCheckView(StaffRequiredMixin,View):
    def post(self,request,pk):
        destination=get_object_or_404(BaleDestination,pk=pk)
        try: BaleClient().get_me();messages.success(request,"اتصال به بله برقرار است.")
        except BaleClientError as exc: messages.error(request,str(exc))
        record_audit(actor=request.user,operation="bale_connection_check",instance=destination)
        return redirect("monitoring:bale_destination_list")


class BaleTestMessageView(StaffRequiredMixin,View):
    def post(self,request,pk):
        destination=get_object_or_404(BaleDestination,is_active=True,pk=pk)
        try: BaleClient().send_message(destination.chat_id,"پیام آزمایشی سامانه رصد ترند") ;messages.success(request,"پیام آزمایشی ارسال شد.")
        except BaleClientError as exc: messages.error(request,str(exc))
        record_audit(actor=request.user,operation="bale_test_message",instance=destination)
        return redirect("monitoring:bale_destination_list")



class AutomationRunDetailView(StaffRequiredMixin,TemplateView):
    template_name="monitoring/automation_run_detail.html"
    def get_context_data(self,**kwargs):
        c=super().get_context_data(**kwargs)
        run=get_object_or_404(AutomationRun.objects.select_related("telegram_run","x_run","trend_analysis_run").prefetch_related("selected_sources", "selected_topics"),pk=self.kwargs["pk"])
        c["run"]=run
        c["run_trends"]=TrendCluster.objects.none()
        if run.trend_analysis_run_id:
            c["run_trends"]=(TrendCluster.objects.filter(
                last_analysis_run_id=run.trend_analysis_run_id,
                merged_into__isnull=True,
                status__in=(TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED),
            ).select_related("topic", "representative__source").prefetch_related("items__content_item__source"))
        return c


class StartAutomationPipelineView(StaffRequiredMixin,View):
    def post(self,request):
        run=start_manual_pipeline()
        record_audit(actor=request.user,operation="automation_pipeline_manual",instance=run)
        if run.status==AutomationRun.Status.PENDING: execute_automation_pipeline.delay(run.pk)
        return redirect("monitoring:automation_run_detail",pk=run.pk)


class ToggleAutomationView(StaffRequiredMixin,View):
    def post(self,request):
        setting=SystemSetting.get_solo();setting.automation_enabled=not setting.automation_enabled;setting.save(update_fields=["automation_enabled","updated_at"])
        record_audit(actor=request.user,operation="automation_toggle",instance=setting)
        next_url = request.POST.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("monitoring:dashboard")


class AutomationRunDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if delete_single_automation_run(pk, actor=request.user):
            messages.success(request, "اجرای رصد خودکار با موفقیت حذف شد.")
        else:
            messages.error(request, "اجرای مورد نظر یافت نشد یا در حال انجام است و امکان حذف آن وجود ندارد.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:automation_run_list")


class AutomationRunStopView(StaffRequiredMixin, View):
    def post(self, request, pk):
        if stop_automation_run(pk, actor=request.user):
            messages.success(request, "رصد در حال اجرا با موفقیت متوقف شد.")
        else:
            messages.error(request, "امکان توقف این رصد وجود ندارد یا این رصد در حال اجرا نیست.")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:automation_run_list")


class AutomationRunBulkDeleteView(StaffRequiredMixin, View):
    def post(self, request):
        ids = request.POST.getlist("selected_ids")
        if not ids:
            messages.warning(request, "هیچ رصد خودکاری برای حذف انتخاب نشده است.")
        else:
            count = bulk_delete_automation_runs(ids, actor=request.user)
            if count > 0:
                messages.success(request, f"{count} اجرای رصد خودکار با موفقیت حذف شد.")
            else:
                messages.warning(request, "هیچ اجرای قابل حذفی یافت نشد (اجراهای در حال اجرا یا در انتظار حذف نمی‌شوند).")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
        if next_url and (next_url.startswith("/") or next_url.startswith("http://127.0.0.1") or next_url.startswith("http://localhost")):
            return redirect(next_url)
        return redirect("monitoring:automation_run_list")

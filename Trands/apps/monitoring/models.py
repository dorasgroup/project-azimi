from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from datetime import timedelta
from urllib.parse import urlparse
import re


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("زمان به‌روزرسانی", auto_now=True)

    class Meta:
        abstract = True


class Topic(TimeStampedModel):
    name = models.CharField("نام", max_length=150, unique=True)
    description = models.TextField("توضیحات", blank=True)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        verbose_name = "موضوع"
        verbose_name_plural = "موضوع‌ها"
        ordering = ("name",)

    def __str__(self):
        return self.name


class Keyword(TimeStampedModel):
    class MatchType(models.TextChoices):
        EXACT = "exact", "عبارت دقیق"
        CONTAINS = "contains", "شامل‌شدن در متن"
        HASHTAG = "hashtag", "هشتگ"

    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="keywords", verbose_name="موضوع")
    phrase = models.CharField("عبارت", max_length=200)
    match_type = models.CharField("نوع تطبیق", max_length=16, choices=MatchType.choices)
    weight = models.PositiveSmallIntegerField("وزن", default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    is_active = models.BooleanField("فعال", default=True)
    is_excluded = models.BooleanField("عبارت مستثنا", default=False)

    class Meta:
        verbose_name = "کلیدواژه"
        verbose_name_plural = "کلیدواژه‌ها"
        ordering = ("topic__name", "phrase")
        constraints = [models.UniqueConstraint(fields=("topic", "phrase", "match_type"), name="unique_keyword_per_topic_match")]

    def __str__(self):
        return f"{self.topic}: {self.phrase}"


class SourceGroup(TimeStampedModel):
    name = models.CharField("نام", max_length=150, unique=True)
    description = models.TextField("توضیحات", blank=True)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        verbose_name = "گروه منبع"
        verbose_name_plural = "گروه‌های منابع"
        ordering = ("name",)

    def __str__(self):
        return self.name


class Source(TimeStampedModel):
    class Platform(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        X = "x", "X"

    class Importance(models.TextChoices):
        GENERAL = "general", "عمومی"
        SPECIAL = "special", "خاص"

    platform = models.CharField("پلتفرم", max_length=16, choices=Platform.choices)
    display_name = models.CharField("نام نمایشی", max_length=150, blank=True)
    identifier = models.CharField("شناسه یا username", max_length=200)
    public_url = models.URLField("نشانی عمومی", blank=True)
    discovered_name = models.CharField("نام کشف‌شده از کانال", max_length=150, blank=True)
    group = models.ForeignKey(SourceGroup, on_delete=models.PROTECT, related_name="sources", null=True, blank=True, verbose_name="گروه منبع")
    importance = models.CharField("نوع منبع", max_length=12, choices=Importance.choices, default=Importance.GENERAL)
    weight = models.PositiveSmallIntegerField("وزن", default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        verbose_name = "منبع"
        verbose_name_plural = "منابع"
        ordering = ("platform", "display_name")
        constraints = [models.UniqueConstraint(fields=("platform", "identifier"), name="unique_source_platform_identifier")]

    TELEGRAM_PUBLIC_HOSTS = {"t.me", "telegram.me"}
    TELEGRAM_CHANNEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{5,32}$")
    X_PUBLIC_HOSTS = {"x.com", "twitter.com"}
    X_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

    @classmethod
    def telegram_identifier_from_public_url(cls, value):
        value = (value or "").strip()
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in cls.TELEGRAM_PUBLIC_HOSTS:
            raise ValidationError("فقط نشانی عمومی HTTPS به‌شکل https://t.me/s/channelname یا https://telegram.me/s/channelname مجاز است.")
        if parsed.port is not None or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValidationError("نشانی کانال Telegram نباید شامل پورت، اطلاعات ورود، query یا fragment باشد.")
        if parsed.path.count("/") != 2:
            raise ValidationError("نشانی کانال باید دقیقاً به‌شکل https://t.me/s/channelname باشد.")
        _, marker, identifier = parsed.path.split("/")
        if marker != "s" or not cls.TELEGRAM_CHANNEL_IDENTIFIER.fullmatch(identifier):
            raise ValidationError("فقط کانال عمومی با بخش /s/ و username معتبر Telegram پذیرفته می‌شود.")
        return identifier.casefold()

    @staticmethod
    def normalize_identifier(value):
        return (value or "").strip().lstrip("@").strip().casefold()

    @classmethod
    def x_identifier_from_input(cls, value):
        value = (value or "").strip()
        if value.startswith(("http://", "https://")):
            parsed = urlparse(value)
            if parsed.scheme != "https" or parsed.hostname not in cls.X_PUBLIC_HOSTS:
                raise ValidationError("فقط URL عمومی HTTPS حساب X پذیرفته می‌شود.")
            if parsed.port is not None or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValidationError("نشانی حساب X نباید شامل پورت، اطلاعات ورود، query یا fragment باشد.")
            if parsed.path.count("/") != 1:
                raise ValidationError("نشانی حساب X باید دقیقاً به‌شکل https://x.com/username باشد.")
            value = parsed.path.removeprefix("/")
        identifier = cls.normalize_identifier(value)
        if not cls.X_HANDLE.fullmatch(identifier) or identifier in {"status", "search", "i", "home", "explore", "settings"}:
            raise ValidationError("فقط handle معتبر حساب X پذیرفته می‌شود.")
        return identifier

    @staticmethod
    def x_public_url(identifier):
        return f"https://x.com/{identifier}"

    def clean(self):
        super().clean()
        if self.platform == self.Platform.TELEGRAM:
            try:
                self.identifier = self.telegram_identifier_from_public_url(self.public_url)
            except ValidationError as exc:
                raise ValidationError({"public_url": exc.messages}) from exc
        elif self.platform == self.Platform.X:
            try:
                self.identifier = self.x_identifier_from_input(self.public_url or self.identifier)
                self.public_url = self.x_public_url(self.identifier)
            except ValidationError as exc:
                raise ValidationError({"identifier": exc.messages}) from exc
        else:
            self.identifier = self.normalize_identifier(self.identifier)
        if not self.identifier:
            raise ValidationError({"identifier": "شناسه منبع نمی‌تواند خالی باشد."})

    def save(self, *args, **kwargs):
        if self.platform == self.Platform.TELEGRAM:
            self.identifier = self.telegram_identifier_from_public_url(self.public_url)
        elif self.platform == self.Platform.X:
            self.identifier = self.x_identifier_from_input(self.public_url or self.identifier)
            self.public_url = self.x_public_url(self.identifier)
        else:
            self.identifier = self.normalize_identifier(self.identifier)
        return super().save(*args, **kwargs)

    @property
    def resolved_name(self):
        return self.display_name or self.discovered_name or self.identifier

    def __str__(self):
        return self.resolved_name


class CollectionRun(models.Model):
    class RunType(models.TextChoices):
        MANUAL = "manual", "دستی"
        SCHEDULED = "scheduled", "زمان‌بندی‌شده"
        RECENT_DAYS = "recent_days", "رصد روزهای گذشته"

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار"
        RUNNING = "running", "در حال اجرا"
        SUCCESS = "success", "موفق"
        PARTIAL = "partial", "ناقص"
        FAILED = "failed", "ناموفق"
        CANCELLED = "cancelled", "لغوشده"

    run_type = models.CharField("نوع اجرا", max_length=16, choices=RunType.choices)
    status = models.CharField("وضعیت", max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField("زمان شروع", null=True, blank=True)
    finished_at = models.DateTimeField("زمان پایان", null=True, blank=True)
    target_sources_count = models.PositiveIntegerField("تعداد منابع هدف", default=0)
    received_content_count = models.PositiveIntegerField("محتوای دریافت‌شده", default=0)
    new_content_count = models.PositiveIntegerField("محتوای جدید", default=0)
    duplicate_content_count = models.PositiveIntegerField("محتوای تکراری", default=0)
    updated_content_count = models.PositiveIntegerField("محتوای به‌روزشده", default=0)
    error_summary = models.CharField("خلاصه خطا", max_length=500, blank=True)
    cancel_requested_at = models.DateTimeField("زمان درخواست لغو", null=True, blank=True)
    requested_start_at = models.DateTimeField("شروع بازه درخواستی", null=True, blank=True)
    requested_end_at = models.DateTimeField("پایان بازه درخواستی", null=True, blank=True)
    selected_topics = models.ManyToManyField(Topic, blank=True, related_name="collection_runs", verbose_name="موضوع‌های انتخاب‌شده")

    class Meta:
        verbose_name = "اجرای جمع‌آوری"
        verbose_name_plural = "اجراهای جمع‌آوری"
        ordering = ("-started_at", "-id")

    @property
    def name(self):
        timestamp = self.started_at or self.finished_at
        if not timestamp:
            return "رصد در انتظار شروع"
        return f"رصد {timezone.localtime(timestamp):%Y/%m/%d، %H:%M}"

    def __str__(self):
        return self.name

    @property
    def is_terminal(self):
        return self.status in {
            self.Status.SUCCESS,
            self.Status.PARTIAL,
            self.Status.FAILED,
            self.Status.CANCELLED,
        }

    @property
    def progress_completed_count(self):
        terminal = {
            CollectionRunSource.Status.SUCCESS,
            CollectionRunSource.Status.PARTIAL,
            CollectionRunSource.Status.FAILED,
            CollectionRunSource.Status.SKIPPED,
            CollectionRunSource.Status.CANCELLED,
        }
        return self.source_results.filter(status__in=terminal).count()

    @property
    def progress_percent(self):
        total = self.target_sources_count
        if not total:
            return 100 if self.is_terminal else 0
        return max(0, min(100, int((self.progress_completed_count / total) * 100)))

    @property
    def current_source_result(self):
        return self.source_results.select_related("source").filter(
            status=CollectionRunSource.Status.RUNNING
        ).first()


class SystemSetting(models.Model):
    DEFAULT_PK = 1
    singleton = models.BooleanField(default=True, unique=True, editable=False)
    collection_interval_minutes = models.PositiveSmallIntegerField("فاصله پیش‌فرض رصد (دقیقه)", default=60, validators=[MinValueValidator(5), MaxValueValidator(1440)])
    automation_enabled = models.BooleanField("رصد خودکار فعال", default=False)
    automation_interval_minutes = models.PositiveSmallIntegerField("فاصله رصد خودکار (دقیقه)", default=60, validators=[MinValueValidator(5), MaxValueValidator(10080)])
    automation_sources = models.ManyToManyField(Source, blank=True, related_name="automation_settings", verbose_name="منابع رصد خودکار")

    @property
    def automation_interval_days(self) -> int:
        return max(1, min(7, round(self.automation_interval_minutes / 1440)))
    automation_sources_configured = models.BooleanField("انتخاب منابع خودکار ثبت شده", default=False)
    automation_topics = models.ManyToManyField(Topic, blank=True, related_name="automation_settings", verbose_name="موضوع‌های تحلیل خودکار")
    automation_topics_configured = models.BooleanField("انتخاب موضوع‌های خودکار ثبت شده", default=False)
    trend_window_hours = models.PositiveSmallIntegerField("پنجره تحلیل ترند (ساعت)", default=6, validators=[MinValueValidator(1), MaxValueValidator(168)])
    minimum_independent_sources = models.PositiveSmallIntegerField("حداقل منابع مستقل", default=3, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    minimum_trend_score = models.PositiveSmallIntegerField("حداقل امتیاز ترند", default=70, validators=[MinValueValidator(1), MaxValueValidator(100)])
    retention_days = models.PositiveSmallIntegerField("روزهای نگهداری داده", default=90, validators=[MinValueValidator(1), MaxValueValidator(3650)])
    trend_similarity_threshold = models.FloatField("آستانه شباهت", default=.78, validators=[MinValueValidator(0), MaxValueValidator(1)])
    trend_min_text_length = models.PositiveSmallIntegerField("حداقل طول متن مشابه", default=30, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    trend_source_weight = models.PositiveSmallIntegerField("وزن منابع مستقل", default=40, validators=[MaxValueValidator(100)])
    trend_platform_weight = models.PositiveSmallIntegerField("وزن چندپلتفرمی", default=20, validators=[MaxValueValidator(100)])
    trend_velocity_weight = models.PositiveSmallIntegerField("وزن سرعت انتشار", default=15, validators=[MaxValueValidator(100)])
    trend_keyword_weight = models.PositiveSmallIntegerField("وزن کلیدواژه", default=15, validators=[MaxValueValidator(100)])
    trend_engagement_weight = models.PositiveSmallIntegerField("وزن تعامل", default=10, validators=[MaxValueValidator(100)])
    sensitive_score_threshold = models.PositiveSmallIntegerField("آستانه حساس", default=75, validators=[MinValueValidator(1), MaxValueValidator(100)])
    updated_at = models.DateTimeField("زمان به‌روزرسانی", auto_now=True)

    class Meta:
        verbose_name = "تنظیمات سامانه"
        verbose_name_plural = "تنظیمات سامانه"

    def __str__(self):
        return "تنظیمات سامانه"

    def clean(self):
        super().clean()
        if self.pk and self.pk != self.DEFAULT_PK:
            raise ValidationError("فقط یک رکورد تنظیمات سامانه مجاز است.")
        if self.__class__.objects.exclude(pk=self.pk).exists():
            raise ValidationError("فقط یک رکورد تنظیمات سامانه مجاز است.")

    @classmethod
    def get_solo(cls):
        setting, _ = cls.objects.get_or_create(pk=cls.DEFAULT_PK)
        return setting


class AuditLog(models.Model):
    SENSITIVE_KEYS = {"secret", "token", "password", "credential", "session", "phone", "api_key", "apikey"}
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="monitoring_audit_logs", verbose_name="کاربر")
    operation = models.CharField("عملیات", max_length=32)
    entity_type = models.CharField("نوع موجودیت", max_length=100)
    entity_id = models.CharField("شناسه موجودیت", max_length=64)
    metadata = models.JSONField("فراداده", default=dict, blank=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        verbose_name = "گزارش ممیزی"
        verbose_name_plural = "گزارش‌های ممیزی"
        ordering = ("-created_at",)

    def clean(self):
        super().clean()
        def validate(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).casefold() in self.SENSITIVE_KEYS:
                        raise ValidationError({"metadata": "فراداده نباید شامل اطلاعات محرمانه باشد."})
                    validate(child)
            elif isinstance(value, list):
                for child in value:
                    validate(child)
        validate(self.metadata)


class ContentItem(TimeStampedModel):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="content_items", verbose_name="منبع")
    external_id = models.CharField("شناسه خارجی پیام", max_length=100)
    published_at = models.DateTimeField("زمان انتشار", db_index=True)
    collected_at = models.DateTimeField("زمان جمع‌آوری", auto_now_add=True)
    raw_text = models.TextField("متن یا caption خام", blank=True)
    normalized_text = models.TextField("متن نرمال‌شده برای جست‌وجو", blank=True)
    public_url = models.URLField("نشانی عمومی پیام", blank=True)
    content_type = models.CharField("نوع محتوا", max_length=40, blank=True)
    media_url = models.URLField("نشانی رسانه", max_length=1000, blank=True)
    ai_status = models.CharField("وضعیت تحلیل هوش مصنوعی", max_length=20, default="pending")
    category = models.CharField("دسته‌بندی", max_length=100, blank=True)
    importance_score = models.PositiveSmallIntegerField("امتیاز اهمیت", default=0, validators=[MaxValueValidator(100)])
    metadata = models.JSONField("فراداده محدود", default=dict, blank=True)

    class Meta:
        verbose_name = "محتوای جمع‌آوری‌شده"
        verbose_name_plural = "محتواهای جمع‌آوری‌شده"
        ordering = ("-published_at",)
        constraints = [models.UniqueConstraint(fields=("source", "external_id"), name="unique_content_source_external_id")]
        indexes = [models.Index(fields=("source", "published_at"), name="content_source_published_idx")]

    @property
    def shared_cluster(self):
        """Return the active trend cluster this item belongs to, if any."""
        for membership in self.cluster_memberships.all():
            cluster = membership.cluster
            if cluster.merged_into_id is None and cluster.status in {TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED}:
                return cluster
        return None

    def save(self, *args, **kwargs):
        if self.raw_text and "\x00" in self.raw_text:
            self.raw_text = self.raw_text.replace("\x00", "")
        if self.normalized_text and "\x00" in self.normalized_text:
            self.normalized_text = self.normalized_text.replace("\x00", "")
        super().save(*args, **kwargs)



class CollectionCursor(TimeStampedModel):
    source = models.OneToOneField(Source, on_delete=models.CASCADE, related_name="collection_cursor", verbose_name="منبع")
    last_external_id = models.CharField("آخرین شناسه پیام", max_length=100, blank=True)
    last_published_at = models.DateTimeField("آخرین زمان انتشار", null=True, blank=True)
    last_successful_run_at = models.DateTimeField("آخرین اجرای موفق", null=True, blank=True)

    class Meta:
        verbose_name = "نشانگر جمع‌آوری"
        verbose_name_plural = "نشانگرهای جمع‌آوری"


class CollectionRunSource(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار"
        RUNNING = "running", "در حال اجرا"
        SUCCESS = "success", "موفق"
        PARTIAL = "partial", "ناقص"
        FAILED = "failed", "ناموفق"
        SKIPPED = "skipped", "ردشده"
        CANCELLED = "cancelled", "لغوشده"

    collection_run = models.ForeignKey(CollectionRun, on_delete=models.CASCADE, related_name="source_results", verbose_name="اجرای جمع‌آوری")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="collection_results", verbose_name="منبع")
    status = models.CharField("وضعیت", max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField("زمان شروع", null=True, blank=True)
    finished_at = models.DateTimeField("زمان پایان", null=True, blank=True)
    received_count = models.PositiveIntegerField("دریافت‌شده", default=0)
    new_count = models.PositiveIntegerField("جدید", default=0)
    duplicate_count = models.PositiveIntegerField("تکراری", default=0)
    updated_count = models.PositiveIntegerField("به‌روزشده", default=0)
    parse_error_count = models.PositiveIntegerField("تعداد خطاهای parser", default=0)
    error_summary = models.CharField("خلاصه خطا", max_length=300, blank=True)

    class Meta:
        verbose_name = "نتیجه منبع در اجرا"
        verbose_name_plural = "نتایج منابع در اجرا"
        constraints = [models.UniqueConstraint(fields=("collection_run", "source"), name="unique_run_source")]


class KeywordMatch(models.Model):
    content_item = models.ForeignKey(ContentItem, on_delete=models.CASCADE, related_name="keyword_matches")
    keyword = models.ForeignKey(Keyword, on_delete=models.CASCADE, related_name="content_matches")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="keyword_matches")
    match_type = models.CharField(max_length=16)
    matched_phrase = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("content_item", "keyword"), name="unique_content_keyword_match")]


class TrendAnalysisRun(models.Model):
    class Status(models.TextChoices):
        PENDING="pending", "در انتظار"; RUNNING="running", "در حال اجرا"; SUCCESS="success", "موفق"; PARTIAL="partial", "ناقص"; FAILED="failed", "ناموفق"
    status=models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at=models.DateTimeField(null=True, blank=True); finished_at=models.DateTimeField(null=True, blank=True)
    content_checked_count=models.PositiveIntegerField(default=0); keyword_match_count=models.PositiveIntegerField(default=0)
    clusters_created_count=models.PositiveIntegerField(default=0); clusters_updated_count=models.PositiveIntegerField(default=0); clusters_resolved_count=models.PositiveIntegerField(default=0)
    error_summary=models.CharField(max_length=500, blank=True)
    class Meta: ordering=("-started_at","-id")


class TrendCluster(TimeStampedModel):
    class Status(models.TextChoices):
        CANDIDATE="candidate","کاندید"; TRENDING="trending","ترند"; CONFIRMED="confirmed","تأییدشده"; DISMISSED="dismissed","ردشده"; SUPPRESSED="suppressed","عدم ارسال"; RESOLVED="resolved","پایان‌یافته"
    topic=models.ForeignKey(Topic,on_delete=models.CASCADE,related_name="trend_clusters")
    status=models.CharField(max_length=16,choices=Status.choices,default=Status.CANDIDATE)
    score=models.PositiveSmallIntegerField(default=0,validators=[MaxValueValidator(100)])
    is_sensitive=models.BooleanField(default=False)
    first_published_at=models.DateTimeField(); last_published_at=models.DateTimeField()
    item_count=models.PositiveIntegerField(default=0); independent_source_count=models.PositiveIntegerField(default=0)
    platforms=models.JSONField(default=list,blank=True); representative=models.ForeignKey(ContentItem,on_delete=models.SET_NULL,null=True,blank=True,related_name="represented_clusters")
    score_breakdown=models.JSONField(default=dict,blank=True); last_analysis_run=models.ForeignKey(TrendAnalysisRun,on_delete=models.SET_NULL,null=True,blank=True,related_name="clusters")
    merged_into=models.ForeignKey("self",on_delete=models.SET_NULL,null=True,blank=True,related_name="merged_clusters",verbose_name="ادغام‌شده در")
    class Meta: ordering=("-score","-last_published_at")

    @property
    def has_special_source(self):
        return any(
            membership.content_item.source.importance == Source.Importance.SPECIAL
            for membership in self.items.all()
        )


class ClusterItem(models.Model):
    class LinkType(models.TextChoices): EXACT="exact","یکسان"; NEAR="near_duplicate","مشابه"; URL="shared_url","URL مشترک"; HASHTAG="shared_hashtag","هشتگ مشترک"
    cluster=models.ForeignKey(TrendCluster,on_delete=models.CASCADE,related_name="items")
    content_item=models.ForeignKey(ContentItem,on_delete=models.CASCADE,related_name="cluster_memberships")
    similarity=models.FloatField(default=1); link_type=models.CharField(max_length=20,choices=LinkType.choices); is_representative=models.BooleanField(default=False)
    class Meta: constraints=[models.UniqueConstraint(fields=("cluster","content_item"),name="unique_cluster_content")]


class BaleDestination(TimeStampedModel):
    name=models.CharField("نام مقصد",max_length=150,unique=True)
    chat_id=models.CharField("شناسه گفت‌وگو",max_length=32,unique=True)
    is_active=models.BooleanField("فعال",default=True)
    description=models.TextField("توضیحات",blank=True)

    class Meta:
        verbose_name="مقصد بله";verbose_name_plural="مقصدهای بله";ordering=("name",)

    def clean(self):
        super().clean()
        if not re.fullmatch(r"-?\d{1,30}", (self.chat_id or "").strip()):
            raise ValidationError({"chat_id":"شناسه مقصد بله باید یک عدد معتبر باشد."})

    def __str__(self):
        return self.name


class Delivery(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING="pending","در انتظار";SENDING="sending","در حال ارسال";DELIVERED="delivered","تحویل‌شده";FAILED="failed","ناموفق";SKIPPED="skipped","ردشده"

    destination=models.ForeignKey(BaleDestination,on_delete=models.PROTECT,related_name="deliveries",verbose_name="مقصد بله")
    trend_cluster=models.ForeignKey(TrendCluster,on_delete=models.PROTECT,related_name="deliveries",verbose_name="خوشه ترند")
    report_version=models.PositiveIntegerField("نسخه گزارش",default=1)
    status=models.CharField("وضعیت",max_length=16,choices=Status.choices,default=Status.PENDING)
    attempt_count=models.PositiveIntegerField("تعداد تلاش",default=0)
    last_attempt_at=models.DateTimeField("آخرین تلاش",null=True,blank=True)
    delivered_at=models.DateTimeField("زمان تحویل",null=True,blank=True)
    provider_message_id=models.CharField("شناسه پیام بله",max_length=100,blank=True)
    retry_after_at=models.DateTimeField("زمان تلاش دوباره",null=True,blank=True)
    error_summary=models.CharField("خلاصه خطا",max_length=300,blank=True)

    class Meta:
        verbose_name="تحویل گزارش بله";verbose_name_plural="تحویل‌های گزارش بله";ordering=("-created_at",)
        constraints=[models.UniqueConstraint(fields=("destination","trend_cluster","report_version"),name="unique_bale_delivery_version")]


class IdeaDelivery(TimeStampedModel):
    class IdeaStatus(models.TextChoices):
        PENDING = "pending", "در انتظار"
        SENDING = "sending", "در حال ارسال"
        ACCEPTED = "accepted", "دریافت‌شده"
        FAILED = "failed", "ناموفق"

    class DifyStatus(models.TextChoices):
        NOT_STARTED = "not_started", "شروع‌نشده"
        QUEUED = "queued", "در صف"
        PROCESSING = "processing", "در حال پردازش"
        DOCUMENT_CREATED = "document_created", "سند ایجاد شد"
        INDEXING = "indexing", "در حال نمایه‌سازی"
        INDEXED = "indexed", "ثبت‌شده"
        FAILED = "failed", "ناموفق"

    trend_cluster = models.ForeignKey(
        TrendCluster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="idea_deliveries",
        verbose_name="خوشه ترند",
    )
    source_system = models.CharField(max_length=64, default="trend-monitor")
    source_trend_id = models.CharField(max_length=200)
    source_version = models.PositiveIntegerField(default=1)
    idempotency_key = models.CharField(max_length=255, unique=True)
    content_hash = models.CharField(max_length=64)
    title = models.CharField(max_length=500)
    topic_name = models.CharField(max_length=255, blank=True)
    score = models.FloatField(null=True, blank=True)
    detected_at = models.DateTimeField()
    payload = models.JSONField(default=dict)
    idea_status = models.CharField(
        max_length=16,
        choices=IdeaStatus.choices,
        default=IdeaStatus.PENDING,
        db_index=True,
    )
    dify_status = models.CharField(
        max_length=32,
        choices=DifyStatus.choices,
        default=DifyStatus.NOT_STARTED,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    dify_completed_at = models.DateTimeField(null=True, blank=True)
    remote_import_id = models.CharField(max_length=64, blank=True)
    remote_upload_id = models.CharField(max_length=64, blank=True)
    remote_ingestion_job_id = models.CharField(max_length=64, blank=True)
    dify_document_id = models.CharField(max_length=64, blank=True)
    last_http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_summary = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("source_system", "source_trend_id", "source_version"),
                name="unique_idea_delivery_source_version",
            ),
            models.UniqueConstraint(
                fields=("content_hash",),
                name="unique_idea_delivery_content_hash",
            ),
        ]


class AutomationState(models.Model):
    singleton=models.BooleanField(default=True,unique=True,editable=False)
    last_started_at=models.DateTimeField(null=True,blank=True)
    lock_expires_at=models.DateTimeField(null=True,blank=True)
    lock_owner=models.CharField(max_length=64,blank=True)
    updated_at=models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        state,_=cls.objects.get_or_create(pk=1,defaults={"singleton":True})
        return state


class AutomationSchedule(TimeStampedModel):
    lookback_hours=models.PositiveSmallIntegerField("بازه محتوای رصد (ساعت)",default=24,validators=[MinValueValidator(3),MaxValueValidator(168)])
    interval_hours=models.PositiveSmallIntegerField("بازه اجرا (ساعت)",validators=[MinValueValidator(3),MaxValueValidator(168)])
    is_active=models.BooleanField("فعال",default=True)
    last_started_at=models.DateTimeField("آخرین شروع",null=True,blank=True)
    sources=models.ManyToManyField(Source,related_name="automation_schedules",verbose_name="منابع")
    topics=models.ManyToManyField(Topic,related_name="automation_schedules",verbose_name="موضوع‌ها")

    class Meta:
        ordering=("-is_active","id")
        verbose_name="رصد خودکار"
        verbose_name_plural="رصدهای خودکار"

    @property
    def next_run_at(self):
        if not self.is_active:
            return None
        if not self.last_started_at:
            return timezone.now()
        return self.last_started_at + timedelta(hours=self.interval_hours)

    @property
    def interval_display(self):
        if self.interval_hours < 24:
            return f"هر {self.interval_hours} ساعت"
        return f"هر {self.interval_hours // 24} روز"

    @property
    def lookback_display(self):
        if self.lookback_hours < 24:
            return f"{self.lookback_hours} ساعت گذشته"
        return f"{self.lookback_hours // 24} روز گذشته"

    def __str__(self):
        return f"رصد خودکار #{self.pk or 'جدید'}"


class AutomationRun(models.Model):
    class Trigger(models.TextChoices): SCHEDULED="scheduled","زمان‌بندی‌شده"; MANUAL="manual_pipeline","اجرای کامل دستی"
    class Status(models.TextChoices): PENDING="pending","در انتظار"; RUNNING="running","در حال اجرا"; SUCCESS="success","موفق"; PARTIAL="partial","ناقص"; FAILED="failed","ناموفق"; SKIPPED="skipped","ردشده"
    trigger=models.CharField(max_length=20,choices=Trigger.choices)
    status=models.CharField(max_length=16,choices=Status.choices,default=Status.PENDING)
    started_at=models.DateTimeField(null=True,blank=True);finished_at=models.DateTimeField(null=True,blank=True)
    telegram_run=models.ForeignKey(CollectionRun,on_delete=models.SET_NULL,null=True,blank=True,related_name="telegram_automation_runs")
    x_run=models.ForeignKey(CollectionRun,on_delete=models.SET_NULL,null=True,blank=True,related_name="x_automation_runs")
    trend_analysis_run=models.ForeignKey(TrendAnalysisRun,on_delete=models.SET_NULL,null=True,blank=True,related_name="automation_runs")
    deliveries_created=models.PositiveIntegerField(default=0);deliveries_delivered=models.PositiveIntegerField(default=0);deliveries_failed=models.PositiveIntegerField(default=0)
    error_summary=models.CharField(max_length=500,blank=True)
    lease_owner=models.CharField(max_length=64,blank=True)
    schedule=models.ForeignKey(AutomationSchedule,on_delete=models.SET_NULL,null=True,blank=True,related_name="runs",verbose_name="رصد خودکار")
    lookback_hours=models.PositiveSmallIntegerField("بازه محتوای رصد (ساعت)",default=24,validators=[MinValueValidator(3),MaxValueValidator(168)])
    selected_sources=models.ManyToManyField(Source,blank=True,related_name="automation_runs",verbose_name="منابع انتخاب‌شده")
    selected_topics=models.ManyToManyField(Topic,blank=True,related_name="automation_runs",verbose_name="موضوع‌های انتخاب‌شده")
    class Meta: ordering=("-started_at","-id")

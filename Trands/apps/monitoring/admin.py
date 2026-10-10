from django.contrib import admin
from .models import AuditLog, AutomationRun, AutomationSchedule, AutomationState, BaleDestination, CollectionCursor, CollectionRun, CollectionRunSource, ContentItem, Delivery, IdeaDelivery, Keyword, Source, SourceGroup, SystemSetting, Topic


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at")
    search_fields = ("name",)


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ("phrase", "topic", "match_type", "weight", "is_active", "is_excluded")
    list_filter = ("match_type", "is_active", "is_excluded")
    search_fields = ("phrase",)


@admin.register(SourceGroup)
class SourceGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at")
    search_fields = ("name",)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("display_name", "platform", "identifier", "importance", "group", "is_active")
    list_filter = ("platform", "importance", "is_active", "group")
    search_fields = ("display_name", "identifier")


@admin.register(CollectionRun)
class CollectionRunAdmin(admin.ModelAdmin):
    list_display = ("run_type", "status", "started_at", "target_sources_count")
    list_filter = ("run_type", "status")
    readonly_fields = ("run_type", "status", "started_at", "finished_at", "target_sources_count", "received_content_count", "new_content_count", "duplicate_content_count", "error_summary")

    def has_add_permission(self, request):
        return False


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SystemSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("operation", "entity_type", "entity_id", "actor", "created_at")
    readonly_fields = ("actor", "operation", "entity_type", "entity_id", "metadata", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(ContentItem)
admin.site.register(CollectionCursor)
admin.site.register(CollectionRunSource)


@admin.register(BaleDestination)
class BaleDestinationAdmin(admin.ModelAdmin):
    list_display=("name","chat_id","is_active","updated_at")
    list_filter=("is_active",)
    search_fields=("name","chat_id")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display=("destination","trend_cluster","report_version","status","attempt_count","delivered_at")
    list_filter=("status","destination")
    readonly_fields=("attempt_count","last_attempt_at","delivered_at","provider_message_id","retry_after_at","error_summary")


@admin.register(IdeaDelivery)
class IdeaDeliveryAdmin(admin.ModelAdmin):
    list_display = ("source_trend_id", "idea_status", "dify_status", "attempt_count", "updated_at")
    list_filter = ("idea_status", "dify_status")
    readonly_fields = ("idempotency_key", "content_hash", "payload", "attempt_count")


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    list_display=("trigger","status","started_at","finished_at","deliveries_created")
    list_filter=("trigger","status")
    readonly_fields=("trigger","status","started_at","finished_at","telegram_run","x_run","trend_analysis_run","deliveries_created","deliveries_delivered","deliveries_failed","error_summary")


admin.site.register(AutomationState)
admin.site.register(AutomationSchedule)

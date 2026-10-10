from django import forms
from django.core.exceptions import ValidationError
from .models import AutomationSchedule, BaleDestination, Keyword, Source, SourceGroup, Topic


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ("name", "description", "is_active")


class KeywordForm(forms.ModelForm):
    class Meta:
        model = Keyword
        fields = ("topic", "phrase", "match_type", "weight", "is_active", "is_excluded")


class SourceGroupForm(forms.ModelForm):
    class Meta:
        model = SourceGroup
        fields = ("name", "description", "is_active")


class SourceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["display_name"].required = False
        self.fields["identifier"].required = False
        self.fields["public_url"].help_text = "برای Telegram فقط نشانی https://t.me/s/channelname یا https://telegram.me/s/channelname پذیرفته می‌شود."

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("platform") == Source.Platform.TELEGRAM:
            try:
                identifier = Source.telegram_identifier_from_public_url(cleaned.get("public_url"))
            except ValidationError as exc:
                self.add_error("public_url", exc.messages[0])
            else:
                self.instance.identifier = identifier
                cleaned["identifier"] = identifier
        return cleaned

    class Meta:
        model = Source
        fields = ("platform", "display_name", "identifier", "public_url", "importance", "weight", "is_active")


class RecentDaysMonitoringForm(forms.Form):
    days = forms.TypedChoiceField(
        label="بازه رصد",
        choices=[(value, f"رصد {value} روز گذشته") for value in range(1, 8)],
        coerce=int,
        initial=1,
    )
    sources = forms.ModelMultipleChoiceField(
        label="منابع مورد رصد",
        queryset=Source.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "حداقل یک منبع را انتخاب کنید."},
    )
    topics = forms.ModelMultipleChoiceField(
        label="موضوع‌های رصد",
        queryset=Topic.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sources"].queryset = Source.objects.filter(is_active=True).order_by("platform", "display_name", "identifier")
        self.fields["topics"].queryset = Topic.objects.filter(is_active=True).order_by("name")


AUTOMATION_LOOKBACK_CHOICES = [
    (3, "رصد ۳ ساعت گذشته"),
    (6, "رصد ۶ ساعت گذشته"),
    (12, "رصد ۱۲ ساعت گذشته"),
] + [(days * 24, f"رصد {days} روز گذشته") for days in range(1, 8)]

AUTOMATION_INTERVAL_CHOICES = [
    (3, "هر ۳ ساعت"),
    (6, "هر ۶ ساعت"),
    (12, "هر ۱۲ ساعت"),
] + [
    (days * 24, f"هر {days} روز" if days > 1 else "هر ۱ روز (روزانه)")
    for days in range(1, 8)
]


class AutomationMonitoringForm(forms.Form):
    hours = forms.TypedChoiceField(
        label="بازه محتوای رصد",
        choices=[("", "انتخاب بازه")] + AUTOMATION_LOOKBACK_CHOICES,
        coerce=int,
        error_messages={"required":"بازه محتوای رصد خودکار را انتخاب کنید."},
    )
    interval_hours = forms.TypedChoiceField(
        label="فاصله اجرای خودکار",
        choices=[("", "انتخاب بازه")] + AUTOMATION_INTERVAL_CHOICES,
        coerce=int,
        error_messages={"required":"بازه زمانی رصد خودکار را انتخاب کنید."},
    )
    sources = forms.ModelMultipleChoiceField(
        label="منابع رصد خودکار",
        queryset=Source.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required":"حداقل یک منبع را انتخاب کنید."},
    )
    topics = forms.ModelMultipleChoiceField(
        label="موضوع‌های تحلیل",
        queryset=Topic.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required":"حداقل یک موضوع را انتخاب کنید."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sources"].queryset = Source.objects.filter(is_active=True).order_by("platform", "display_name", "identifier")
        self.fields["topics"].queryset = Topic.objects.filter(is_active=True).order_by("name")


class AutomationScheduleForm(forms.ModelForm):
    lookback_hours = forms.TypedChoiceField(
        label="بازه محتوای رصد",
        choices=AUTOMATION_LOOKBACK_CHOICES,
        coerce=int,
    )
    interval_hours = forms.TypedChoiceField(
        label="فاصله اجرای خودکار",
        choices=AUTOMATION_INTERVAL_CHOICES,
        coerce=int,
    )

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["sources"].queryset=Source.objects.filter(is_active=True).order_by("platform","display_name","identifier")
        self.fields["topics"].queryset=Topic.objects.filter(is_active=True).order_by("name")

    class Meta:
        model=AutomationSchedule
        fields=("lookback_hours","interval_hours","sources","topics","is_active")
        widgets={"sources":forms.CheckboxSelectMultiple,"topics":forms.CheckboxSelectMultiple}


class BaleDestinationForm(forms.ModelForm):
    class Meta:
        model=BaleDestination
        fields=("name","chat_id","is_active","description")

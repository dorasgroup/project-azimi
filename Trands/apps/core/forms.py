from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

User = get_user_model()


class UserProfileForm(forms.Form):
    username = forms.CharField(
        label="نام کاربری",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username", "dir": "ltr"}),
        help_text="شناسه یکتا برای ورود به سامانه رصد",
    )
    new_password = forms.CharField(
        label="رمز عبور جدید",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password", "dir": "ltr"}),
        help_text="در صورتی که قصد تغییر رمز عبور را ندارید، این قسمت را خالی بگذارید.",
    )
    confirm_password = forms.CharField(
        label="تکرار رمز عبور جدید",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password", "dir": "ltr"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        if user and "initial" not in kwargs:
            kwargs["initial"] = {"username": user.username}
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise ValidationError("نام کاربری نمی‌تواند خالی باشد.")
        qs = User.objects.filter(username=username)
        if self.user:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise ValidationError("این نام کاربری قبلاً توسط کاربر دیگری انتخاب شده است.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")

        if new_password or confirm_password:
            if not new_password:
                self.add_error("new_password", "لطفاً رمز عبور جدید را وارد نمایید.")
            elif not confirm_password:
                self.add_error("confirm_password", "لطفاً تکرار رمز عبور جدید را وارد نمایید.")
            elif new_password != confirm_password:
                self.add_error("confirm_password", "رمز عبور جدید با تکرار آن همخوانی ندارد.")
            elif len(new_password) < 4:
                self.add_error("new_password", "طول رمز عبور جدید باید حداقل ۴ کاراکتر باشد.")
        return cleaned_data

    def save(self):
        if not self.user:
            raise ValueError("کاربر برای ذخیره مشخص نشده است.")
        self.user.username = self.cleaned_data["username"]
        new_password = self.cleaned_data.get("new_password")
        if new_password:
            self.user.set_password(new_password)
        self.user.save()
        return self.user

"""Safe user-facing failures for otherwise successful empty model responses."""

EMPTY_MODEL_MESSAGE = "پاسخی از مدل دریافت نشد. لطفاً دوباره تلاش کنید."
EMPTY_HISTORY_MESSAGE = (
    "برای این پیام، پاسخ مدل خالی برگشته و پاسخی ذخیره نشده است. "
    "لطفاً سؤال را دوباره ارسال کنید."
)


class EmptyModelAnswerError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(EMPTY_MODEL_MESSAGE)

from pydantic import BaseModel, ConfigDict, Field


class BaleUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    is_bot: bool = False
    first_name: str = ""
    username: str | None = None


class BaleChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    type: str = "private"


class BaleDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    file_unique_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class BaleMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: int
    chat: BaleChat
    from_user: BaleUser | None = Field(default=None, alias="from")
    text: str | None = None
    document: BaleDocument | None = None
    caption: str | None = None


class BaleCallbackQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    from_user: BaleUser = Field(alias="from")
    message: BaleMessage | None = None
    data: str | None = None


class BaleUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: BaleMessage | None = None
    callback_query: BaleCallbackQuery | None = None


class BaleWebhookResult(BaseModel):
    accepted: bool = True
    duplicate: bool = False

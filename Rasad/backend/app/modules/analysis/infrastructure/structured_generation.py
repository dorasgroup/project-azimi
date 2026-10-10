"""One bounded same-input regeneration for incomplete/invalid typed model output."""
from dataclasses import replace
import json
from pydantic import BaseModel, ValidationError
from app.modules.analysis.domain.model_contract import ModelGenerationRequest
from app.modules.analysis.domain.source_analysis import PermanentAnalysisError
from app.modules.analysis.infrastructure.model_gateway import ModelOutputTruncatedError
from app.modules.analysis.ports import ModelGateway


async def generate_validated_json(gateway: ModelGateway, request: ModelGenerationRequest,
                                  schema: type[BaseModel]) -> tuple[BaseModel, int]:
    current = request
    for attempt in range(2):
        try:
            generated = await gateway.generate_json(current)
            return schema.model_validate(generated.content), attempt
        except (ValidationError, ModelOutputTruncatedError) as exc:
            if attempt:
                raise PermanentAnalysisError("Invalid native analysis contract after bounded repair") from None
            locations = ([{"loc": [str(part)[:120] for part in error["loc"]], "type": error["type"]}
                          for error in exc.errors(include_input=False, include_context=False)[:12]]
                         if isinstance(exc, ValidationError) else [{"type": "response_budget_exceeded"}])
            current = replace(request, user_prompt=request.user_prompt + "\n\n"
                "پاسخ قبلی ناقص یا ناسازگار با قرارداد بود. از همان ورودی یک JSON کامل تازه و فشرده تولید کن. "
                "همه بخش‌های الزامی را پیش از گسترش جزئیات کامل کن؛ متن را در سقف توکن پاسخ نگه دار. "
                "جزئیات را خلاصه کن، نه اینکه بخش الزامی یا شاهد معتبر را حذف کنی. "
                "در نبود شاهد آرایه خالی مجاز است، نه شاهد یا نشانی ساختگی. خطاهای ساختاری:\n"
                + json.dumps(locations, ensure_ascii=False))
    raise AssertionError("Unreachable bounded generation state")

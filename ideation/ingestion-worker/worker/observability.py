import structlog
from functools import wraps

logger = structlog.get_logger()

class TracerMock:
    def start_as_current_span(self, name: str):
        class SpanContext:
            def __enter__(self): pass
            def __exit__(self, exc_type, exc_val, exc_tb): pass
        return SpanContext()

tracer = TracerMock()

def trace_stage(stage_name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger.info(f"Starting stage: {stage_name}")
            with tracer.start_as_current_span(stage_name):
                return func(*args, **kwargs)
        return wrapper
    return decorator

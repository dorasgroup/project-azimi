FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S egress && adduser -S -G egress egress
WORKDIR /app
COPY --chown=egress:egress proxy.py /app/proxy.py
USER egress
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/__egress__/health', timeout=2)"

CMD ["python", "/app/proxy.py"]

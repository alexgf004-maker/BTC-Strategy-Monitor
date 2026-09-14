FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

RUN adduser --disabled-password --gecos "" monitor \
    && chown -R monitor:monitor /app

USER monitor
EXPOSE 8080

CMD ["python", "-m", "monitor.service"]

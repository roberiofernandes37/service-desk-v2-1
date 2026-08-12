FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /srv/service-desk

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /srv/service-desk/uploads /srv/service-desk/logs /srv/service-desk/backups /srv/service-desk/instance

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "2", "--timeout", "60", "wsgi:app"]

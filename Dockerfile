FROM python:3.12-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends nginx gettext-base wget \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/deploy/start.sh

ENV PYTHONUNBUFFERED=1
ENV UVICORN_HOST=0.0.0.0
ENV PORT=10000

EXPOSE 10000

CMD ["/app/deploy/start.sh"]

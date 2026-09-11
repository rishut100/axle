FROM python:3.13-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt

# runtime
FROM python:3.13-slim

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends dumb-init jq bind9-host libpq5 postgresql-client && \
    rm -rf /var/lib/apt/lists/* && \
    ln -s /usr/bin/dumb-init /usr/local/bin/dumb-init && \
    pip install --no-cache-dir awscli

COPY --from=builder /venv /venv

WORKDIR /app

COPY . .

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

ENV OPT_PATH='/opt'

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
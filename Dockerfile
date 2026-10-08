# Cerase Calc MCP — exact decimal arithmetic for the figures an assistant
# writes in documents and mail.
#
# Exposes 4 tools: calculate / sum / product / round. Standard library
# `decimal` only: no network, no files, no credential.
#
# FastMCP stdio server bridged to HTTP/SSE by mcp-proxy — the same shape as
# every other cerase-* MCP image.
FROM python:3.13.9-slim@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86 AS runtime

# The digest-pinned base lags Debian's security feed, so this stage applies the
# published security upgrades before installing anything.
RUN apt-get update && apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
    && rm /tmp/requirements.txt /tmp/requirements.lock

COPY server.py /app/server.py

# The liveness probe below is a real MCP client, not a socket connect: with the
# stdio child gone mcp-proxy keeps the listener open and answers the handshake
# from what it recorded at its own startup, so the connect went on passing for a
# container with no server left in it. The script's header carries the detail.
COPY scripts/healthcheck.py /app/healthcheck.py

RUN groupadd -r appuser \
 && useradd -r -g appuser -u 1000 -m -d /home/appuser -s /usr/sbin/nologin appuser \
 && chown -R appuser:appuser /app
USER appuser
WORKDIR /home/appuser

EXPOSE 3000

# Image-level liveness — runtime-spawned MCP containers have no compose
# healthcheck, so this is the only signal `docker ps` and doctor see.
#
# It completes the handshake and lists this server's tools, which is the first
# request that has to reach the stdio child. The script keeps itself inside the
# timeout by budget, so the timeout below is its ceiling and not its schedule.
HEALTHCHECK --interval=5m --start-interval=5s --timeout=10s --start-period=60s --retries=2 \
    CMD python3 /app/healthcheck.py || exit 1

ENTRYPOINT ["sh", "-c", "exec mcp-proxy --port 3000 --host 0.0.0.0 --pass-environment -- python /app/server.py"]

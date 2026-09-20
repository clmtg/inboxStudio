FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends imapfilter ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 filter \
    && mkdir /data && chown filter:filter /data
COPY app /app
COPY config.lua /config/config.lua
COPY run.sh /usr/local/bin/run-filter
RUN chmod 755 /usr/local/bin/run-filter
ENV HOME=/home/filter
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER filter
ENTRYPOINT ["/usr/local/bin/run-filter"]

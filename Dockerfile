FROM denoland/deno:bin-2.4.5 AS deno

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    HOME=/data \
    XDG_CACHE_HOME=/data/cache
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --home-dir /data --no-create-home resave \
    && mkdir -p /data \
    && chown resave:resave /data
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY main.py config.py ./
COPY src ./src
USER resave
VOLUME /data
CMD ["python", "main.py"]

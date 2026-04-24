#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/.build/telegram-bot-api-linux-amd64"
IMAGE_NAME="resave-telegram-bot-api-builder:linux-amd64"

mkdir -p "$DIST_DIR" "$BUILD_DIR"

docker build --platform linux/amd64 -t "$IMAGE_NAME" -f - "$ROOT_DIR" <<'DOCKERFILE'
FROM debian:bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cmake \
    g++ \
    git \
    gperf \
    make \
    libssl-dev \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --recursive https://github.com/tdlib/telegram-bot-api.git

WORKDIR /src/telegram-bot-api
RUN cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/out \
  && cmake --build build --target install -j2
DOCKERFILE

docker create --platform linux/amd64 --name resave-telegram-bot-api-extract "$IMAGE_NAME" >/dev/null
trap 'docker rm -f resave-telegram-bot-api-extract >/dev/null 2>&1 || true' EXIT

docker cp resave-telegram-bot-api-extract:/out/bin/telegram-bot-api \
  "$DIST_DIR/telegram-bot-api-linux-amd64"

chmod +x "$DIST_DIR/telegram-bot-api-linux-amd64"
file "$DIST_DIR/telegram-bot-api-linux-amd64"

echo
echo "Built: $DIST_DIR/telegram-bot-api-linux-amd64"
echo "Upload it to the server as: /home/renothing/.local/bin/telegram-bot-api"

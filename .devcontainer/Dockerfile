FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 libgl1 libgbm1 libnss3 libasound2 libportaudio2 libdbus-1-3 \
    libxkbcommon0 libxkbcommon-x11-0 libx11-xcb1 libxcb-cursor0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 libxcb-xkb1 libxcomposite1 libxdamage1 libxrandr2 \
    libxtst6 xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

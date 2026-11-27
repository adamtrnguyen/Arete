FROM mlcivilengineer/anki-desktop-docker:main

# Pre-install Anki into a dedicated venv to avoid runtime downloads.
USER 0

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-venv \
    xdotool \
    libxcb-cursor0 \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/anki-venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Keep this pin equal to the Anki version tested on desktop and in pyproject.toml.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir anki==26.8.1 aqt==26.8.1 PyQt6 PyQt6-WebEngine

# Replace the base launcher (which checks for updates and waits for Enter) with a
# direct headless-safe launch of the pre-installed Anki binary.
RUN printf '%s\n' \
    '#!/bin/bash' \
    'export QT_QUICK_BACKEND=software' \
    'export ANKI_NOHIGHDPI=1' \
    'export QT_WEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu"' \
    'export ANKI_SKIP_UPDATE_CHECK=1' \
    'exec /opt/anki-venv/bin/anki --no-sandbox "$@"' \
    > /usr/local/share/anki/launcher.amd64 && \
    chmod +x /usr/local/share/anki/launcher.amd64

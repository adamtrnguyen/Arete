FROM mlcivilengineer/anki-desktop-docker:main

# Pre-install Anki to avoid runtime downloads
# This might vary depending on how the launcher checks, but putting it in the global or expected venv is a good start.

# Switch to root to install packages
USER 0

# The launcher seems to create a .venv in /usr/local/share/anki or similar.
# Let's try to install globally first, or replicate the venv structure if we can find it.
# Based on logs, it uses python 3.13.

# Install dependencies required for Anki
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    xdotool \
    libxcb-cursor0 \
    && rm -rf /var/lib/apt/lists/*

# Create a venv to avoid system package conflicts
ENV VIRTUAL_ENV=/opt/anki-venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Anki into the venv.
# Keep this pin equal to the Anki the maintainer runs on the desktop. A container two
# years behind tests an API the real client dropped. Checked on PyPI 2026-09-10:
# 26.8.1 is the newest non-beta, and it matches the desktop build.
RUN pip install --upgrade pip && \
    pip install anki==26.8.1 aqt==26.8.1 PyQt6 PyQt6-WebEngine

# Hijack the launcher!
# The original image uses this binary to check for updates and wait for Enter.
# We replace it with a script that simply launches our installed Anki with necessary flags.
# --no-sandbox is critical for QtWebEngine in Docker.
RUN echo '#!/bin/bash\n\
export QT_QUICK_BACKEND=software\n\
export ANKI_NOHIGHDPI=1\n\
export QT_WEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu"\n\
export ANKI_SKIP_UPDATE_CHECK=1\n\
exec /opt/anki-venv/bin/anki --no-sandbox' > /usr/local/share/anki/launcher.amd64 && \
    chmod +x /usr/local/share/anki/launcher.amd64
# We leave it as is or follow base image conventions.

"""Poll the Dockerized AnkiConnect endpoint until it responds."""

import os
import sys
import time

import httpx

ANKI_URL = os.getenv("ANKI_CONNECT_URL", "http://127.0.0.1:8766")
MAX_RETRIES = 30  # 30 retries * 1 second = 30 seconds max wait
DELAY = 1


def check_anki() -> bool:
    urls = [ANKI_URL]
    if "127.0.0.1" in ANKI_URL:
        urls.append(ANKI_URL.replace("127.0.0.1", "0.0.0.0"))

    for url in urls:
        try:
            response = httpx.post(url, json={"action": "version", "version": 6}, timeout=1.0)
            if response.status_code == 200:
                print(f"Anki is ready at {url}! Version: {response.json()}")
                return True
        except (httpx.HTTPError, ValueError):
            continue
    return False


def main() -> None:
    print(f"Waiting for Anki (trying {ANKI_URL} and/or 0.0.0.0)...")
    sys.stdout.flush()
    for i in range(MAX_RETRIES):
        if check_anki():
            sys.exit(0)
        time.sleep(DELAY)
        if (i + 1) % 5 == 0:
            print(f"Still waiting... {i + 1}/{MAX_RETRIES} attempts")
            sys.stdout.flush()

    print("Timed out waiting for Anki.")
    sys.exit(1)


if __name__ == "__main__":
    main()

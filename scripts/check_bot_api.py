#!/usr/bin/env python3
"""Smoke-test the internal bot API without requiring curl in the Docker image."""

import json
import os
from urllib.request import Request, urlopen


def main():
    request = Request(
        "http://127.0.0.1:8000/api/bot/v1/",
        data=b'{"action":"ping","payload":{}}',
        headers={
            "Host": os.environ["DOMAIN"],
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['DELTAJANEBI_BOT_API_KEY']}",
        },
        method="POST",
    )

    with urlopen(request, timeout=15) as response:
        payload = json.load(response)

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SystemExit("Bot API ping did not return ok=true")
    print("Bot API ping: OK")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Check Django's health endpoint AND rendered homepage before declaring install complete."""
import os
from urllib.request import Request, urlopen


def main():
    domain = os.environ["DOMAIN"]
    for path in ("/health/", "/"):
        request = Request(
            "http://127.0.0.1:8000" + path,
            headers={"Host": domain},
        )
        with urlopen(request, timeout=15) as response:
            response.read(1024)
            if response.status != 200:
                raise RuntimeError(f"{path} returned HTTP {response.status}")
        print(f"Django {path}: HTTP 200")


if __name__ == "__main__":
    main()

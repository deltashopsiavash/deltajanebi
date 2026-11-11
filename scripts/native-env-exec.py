#!/usr/bin/env python3
"""Run a command with a private service EnvironmentFile, without shell sourcing.

Used for one-off Django management commands under the native systemd user.
Never prints secret values; preserves all pre-existing environment variables.
"""
import os
from pathlib import Path
import re
import shlex
import sys


def load_env(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            raise ValueError("Invalid native environment configuration")
        parsed = shlex.split(value.strip(), posix=True)
        if len(parsed) != 1:
            raise ValueError("Invalid environment value format for " + key)
        result[key.strip()] = parsed[0]
    return result


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: native-env-exec.py /etc/deltajanebi/native.env COMMAND [ARGS...]")
    env = os.environ.copy()
    env.update(load_env(sys.argv[1]))
    os.execvpe(sys.argv[2], sys.argv[2:], env)


if __name__ == "__main__":
    main()

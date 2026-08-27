#!/usr/bin/env bash
set -Eeuo pipefail

# Backward-compatible wrapper. New updates should use update-site.sh directly.
exec bash <(curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/update-site.sh)

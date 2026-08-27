#!/usr/bin/env bash
set -Eeuo pipefail

# Backward-compatible wrapper. New installs should use install-site.sh directly.
exec bash <(curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/install-site.sh)

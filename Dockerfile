FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Use Python's standard library for health checks and API smoke tests.
# No apt-get or curl is required during the image build.

COPY requirements.txt .
# APT-independent Python dependencies. Retry using separate HTTPS indexes if
# the first index is blocked or times out on restricted networks.
# Set DELTA_PIP_INDEXES in the host environment to override the order.
ARG PIP_INDEXES="https://mirrors.aliyun.com/pypi/simple/ https://pypi.tuna.tsinghua.edu.cn/simple/ https://pypi.org/simple/"
# Mount wheel files as a separate build context: they are NOT copied into the final image.
# The installer builds these wheels with the server's host network, bypassing
# broken container bridge/NAT routes and privileged BuildKit host networking.
RUN --mount=type=bind,from=wheelhouse,target=/wheels,readonly \
    set -eu; \
    if [ -f /wheels/.requirements.sha256 ]; then \
        echo "==> Installing cached wheels without network"; \
        python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt; \
        exit 0; \
    fi; \
    echo "==> Wheelhouse empty; trying HTTPS indexes (manual Docker build)"; \
    for index in $PIP_INDEXES; do \
        echo "==> Trying Python package index: $index"; \
        if python -m pip install --no-cache-dir --timeout 25 --retries 1 --index-url "$index" -r requirements.txt; then \
            exit 0; \
        fi; \
        echo "WARNING: $index failed; trying next HTTPS mirror" >&2; \
    done; \
    echo "ERROR: No configured Python package index could be reached. Check container outbound HTTPS/DNS or set DELTA_PIP_INDEXES." >&2; \
    exit 1

COPY . .
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]

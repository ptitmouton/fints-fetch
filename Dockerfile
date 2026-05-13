# syntax=docker/dockerfile:1
#
# Multi-stage build:
#   - `builder` installs the project into an isolated venv
#   - the runtime image copies that venv into a clean slim image, drops
#     privileges, and runs `gls-fints` as the entrypoint
#
# Build:
#   docker build -t gls-fints .
#
# Run interactively (TAN prompts go to stdin):
#   docker run --rm -it \
#     -e GLS_USER=YourVRNetKey \
#     -e GLS_PIN=YourOnlineBankingPIN \
#     -e FINTS_PRODUCT_ID=YourRegisteredProductID \
#     -v gls-fints-state:/state \
#     -e FINTS_STATE_FILE=/state/fints_state \
#     gls-fints --days 30
#
# The named volume is needed if you pass --persist-state, so the bootstrap
# (TAN mechanism / medium choice) survives container restarts.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build tools only in the builder stage. lxml ships wheels for
# modern Pythons so we don't need libxml2-dev/libxslt-dev here.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what's needed for resolving dependencies first, so the
# expensive pip install layer is cached when only source changes.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FINTS_STATE_FILE=/state/fints_state

# Non-root user. UID/GID 10001 to avoid collisions with host users.
RUN groupadd --system --gid 10001 app && \
    useradd  --system --uid 10001 --gid app --home-dir /home/app --shell /usr/sbin/nologin app && \
    install -d -o app -g app /home/app /state

COPY --from=builder /opt/venv /opt/venv

USER app
WORKDIR /home/app

ENTRYPOINT ["gls-fints"]
CMD ["--help"]

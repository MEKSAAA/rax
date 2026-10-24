# Trusted operator build only (scripts/build_agent_image.py). Agent containers never install from
# the network: the wheels are downloaded and hash-locked by the build script before this runs.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /requirements.lock
RUN --mount=type=bind,source=wheels,target=/wheels \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels --require-hashes -r /requirements.lock \
    && python -m pip check
ENV PIP_NO_INDEX=1 HF_HUB_OFFLINE=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
# No simulator, repository, secret or Docker socket is copied. Launchers always override the UID
# and enforce the actual runtime restrictions.
USER 65534:65534

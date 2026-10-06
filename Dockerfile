# Builder tools never reach the serving image. Build a wheel with `pdm build` first.
FROM docker.io/library/python@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS dependencies
ARG REQUIREMENTS=container/requirements-amd64.txt
COPY ${REQUIREMENTS} /build/requirements.txt
RUN python -m pip install --require-hashes --only-binary=:all: --no-compile --no-cache-dir --target /dependencies -r /build/requirements.txt
COPY dist/*.whl /build/
RUN python -m pip install --no-deps --no-index --no-compile --target /dependencies /build/*.whl

# Minimal glibc Python 3.14, with no shell or package installer in the runtime.
FROM cgr.dev/chainguard/python@sha256:b7af1ae90e2fcfb5c32be03908e74d32fdfd64156c2b7c535bd3e497e7846d84
ARG VERSION
ARG REVISION
ARG SOURCE
LABEL org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.source=$SOURCE
COPY --from=dependencies /dependencies /app/dependencies
ENV PYTHONPATH=/app/dependencies \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/model-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    NCI_SI_DATA_DIR=/data \
    NCI_SI_EMBEDDING_PROVIDER=sentence-transformers \
    NCI_SI_TRANSPORT=streamable-http \
    NCI_SI_HTTP_HOST=0.0.0.0 \
    NCI_SI_HTTP_SESSIONS=stateless \
    NCI_SI_HTTP_REQUIRE_INDEX=1
USER 65532:65532
EXPOSE 8000
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/python", "-m", "nci_si_mcp.container_entry"]

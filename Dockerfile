# Build a wheel with `pdm build` first. Both stages use AL2023's Python 3.14.
FROM public.ecr.aws/amazonlinux/amazonlinux@sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061 AS base
RUN dnf -y upgrade --refresh
RUN dnf -y install python3.14 libgomp
RUN dnf clean all

FROM base AS dependencies
RUN dnf -y install python3.14-pip
RUN python3.14 -m venv /opt/venv
ARG REQUIREMENTS=container/requirements-amd64.txt
COPY ${REQUIREMENTS} /build/requirements.txt
RUN /opt/venv/bin/python -m pip install --require-hashes --only-binary=:all: --no-compile --no-cache-dir -r /build/requirements.txt
COPY dist/*.whl /build/
RUN /opt/venv/bin/python -m pip install --no-deps --no-index --no-compile /build/*.whl

FROM base AS runtime
ARG VERSION
ARG REVISION
ARG SOURCE
LABEL org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.source=$SOURCE
COPY --from=dependencies /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
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
ENTRYPOINT ["/opt/venv/bin/python", "-m", "nci_si_mcp.container_entry"]

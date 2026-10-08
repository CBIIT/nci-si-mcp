FROM public.ecr.aws/amazonlinux/amazonlinux@sha256:12052e9b5d3fd85769abbdd863dd038e1890c9ace31d5fdbe1afa78eda97d061
RUN dnf -y upgrade --refresh
RUN dnf -y install python3.14
RUN dnf clean all
COPY site /site
COPY static_server.py /app/static_server.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=5s CMD ["python3.14", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).read()"]
ENTRYPOINT ["python3.14", "/app/static_server.py", "--directory", "/site", "--host", "0.0.0.0", "--port", "8080"]

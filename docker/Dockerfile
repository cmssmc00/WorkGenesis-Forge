# Operator-built example only. The runtime NEVER builds, pulls, or installs images.
# Supply an already-reviewed official Python base by immutable digest.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
WORKDIR /output
ENTRYPOINT ["python3"]

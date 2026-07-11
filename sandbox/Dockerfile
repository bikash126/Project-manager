# Sandbox image for Developer/QA task execution.
# Runs with --network none at task time, so test dependencies are baked in here.
#
#   docker build -t pm-sandbox:latest sandbox/
FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==8.* && \
    useradd --create-home sandbox

USER sandbox
WORKDIR /workspace

# Sandbox image for Developer/QA/Security task execution.
# Runs with --network none (or an egress allowlist) at task time, so test and
# scanner dependencies are baked in here.
#
#   docker build -t pm-sandbox:latest sandbox/
FROM python:3.12-slim

# Phase 3 security stack: SAST (semgrep), dependency audit (pip-audit),
# secrets (gitleaks). All free; run in the sandbox on the workspace.
ARG GITLEAKS_VERSION=8.18.4
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && pip install --no-cache-dir pytest==8.* semgrep pip-audit \
    && curl -sSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
       | tar -xz -C /usr/local/bin gitleaks \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home sandbox

USER sandbox
WORKDIR /workspace

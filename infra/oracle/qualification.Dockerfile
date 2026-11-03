# Credential-free, disposable owner tool; never an application runtime service.
FROM node:24.22.1-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS node
FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944 AS complete
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ca-certificates python3 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /tmp/npm
WORKDIR /opt/onesa-ai-robot
COPY onesa-ai-robot-frontend/package.json onesa-ai-robot-frontend/package-lock.json ./onesa-ai-robot-frontend/
COPY onesa-ai-robot-frontend/vendor ./onesa-ai-robot-frontend/vendor
RUN node /tmp/npm/bin/npm-cli.js ci --ignore-scripts --prefix onesa-ai-robot-frontend && node onesa-ai-robot-frontend/node_modules/playwright/cli.js install --with-deps chromium firefox webkit && ln -s onesa-ai-robot-frontend/node_modules node_modules && rm -rf /tmp/npm /root/.npm /var/lib/apt/lists/* /usr/local/lib/node_modules/npm /opt/yarn-* && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg
# The explicit Node/Python probe never starts the base image's service manager.
# Remove that unused Go executable rather than carrying its vulnerable runtime.
RUN rm -f /usr/bin/pebble
COPY scripts/release ./scripts/release
# Remove build history/tooling from the final host-mounted credential boundary.
FROM scratch
COPY --from=complete / /
WORKDIR /opt/onesa-ai-robot
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG SOURCE_COMMIT
ARG SOURCE_ARCHIVE_SHA256
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT org.onesa.source-archive-sha256=$SOURCE_ARCHIVE_SHA256 org.onesa.browser="Playwright locked Chromium Firefox WebKit"
ENTRYPOINT ["node","/opt/onesa-ai-robot/scripts/release/oracle_robot_browser.mjs"]

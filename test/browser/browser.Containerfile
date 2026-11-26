# The browser of the tests: Playwright's image (Chromium and its fonts),
# pinned by digest. Raise the version, the digest and the package together.
FROM mcr.microsoft.com/playwright:v1.65.1-noble@sha256:1507cbfc550a63a6b7396791ca4f00df17beb56342d2447b3f106c306f525579
WORKDIR /opt/browser
RUN npm init -y >/dev/null && npm install --no-audit --no-fund playwright@1.65.1
ENV NODE_PATH=/opt/browser/node_modules

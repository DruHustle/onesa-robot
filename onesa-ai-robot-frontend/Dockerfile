FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build
WORKDIR /build
COPY package*.json ./
COPY vendor ./vendor
RUN npm ci --ignore-scripts
COPY . ./
RUN npm run build
FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53
WORKDIR /app
RUN apk upgrade --no-cache && apk add --no-cache 'musl>=1.2.6-r3' 'musl-utils>=1.2.6-r3' && rm -rf /usr/local/lib/node_modules/npm /opt/yarn-* && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg
COPY --chown=node:node server.mjs ./
COPY --chown=node:node lib ./lib
COPY --from=build --chown=node:node /build/dist ./dist
ENV NODE_OPTIONS="--import=/app/lib/tls-trust.mjs"
USER node
EXPOSE 3431
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||'3431')+'/').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.mjs"]

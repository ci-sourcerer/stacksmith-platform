# syntax=docker/dockerfile:1.7

FROM node:24-alpine AS development
WORKDIR /web
COPY web/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY --chown=node:node web/ ./
RUN chown node:node /web
ENV VITE_CACHE_DIR=/tmp/vite
USER node
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8080", "--configLoader", "runner"]

FROM development AS build
USER root
RUN npm run build

FROM nginx:1.30-alpine AS runtime
COPY --from=build /web/dist /usr/share/nginx/html
COPY deploy/frontend/nginx.conf.template /etc/nginx/platform.conf.template
COPY deploy/frontend/start.sh /usr/local/bin/platform-frontend
RUN chmod +x /usr/local/bin/platform-frontend
ENV API_UPSTREAM=http://api:8000
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/platform-frontend"]

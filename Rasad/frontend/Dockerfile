FROM node:24.3.0-alpine3.22 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_API_URL=/web-api
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginx:1.31.4-alpine

RUN apk upgrade --no-cache

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html/web

USER nginx

EXPOSE 5173

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:5173/web/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

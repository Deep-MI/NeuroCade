FROM node:22-alpine AS client-build

WORKDIR /client
COPY client/package.json client/package-lock.json ./
RUN npm ci
COPY client ./
ARG VITE_API_URL=/api/app
ARG VITE_CLERK_PUBLISHABLE_KEY=
ARG VITE_CLERK_JWT_TEMPLATE=
ARG VITE_LOCAL_AUTH_ENABLED=true
ENV VITE_API_URL=${VITE_API_URL} \
    VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY} \
    VITE_CLERK_JWT_TEMPLATE=${VITE_CLERK_JWT_TEMPLATE} \
    VITE_LOCAL_AUTH_ENABLED=${VITE_LOCAL_AUTH_ENABLED}
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=client-build /client/dist /usr/share/nginx/html
EXPOSE 80

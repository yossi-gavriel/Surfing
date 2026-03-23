FROM node:20-slim AS build

WORKDIR /app/frontend

COPY frontend/package*.json /app/frontend/
RUN npm install

COPY frontend/ /app/frontend/
RUN npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist/frontend/browser /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

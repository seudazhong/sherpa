# Sherpa frontend (Vite dev server; proxies /auth,/sessions,... to the web service).
# For production you would `npm run build` and serve dist/ behind a static server.
FROM node:20-bookworm-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

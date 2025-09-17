FROM nginx:alpine

# Copy static files (host-container sync)
COPY . /usr/share/nginx/html/

# Copy SPA-enabled nginx config (immutable)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Ensure proper permissions
RUN chmod -R 755 /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

FROM python:3.11-slim

WORKDIR /app

# Copy synced host files
COPY backend/api_gateway/app/ /app/
COPY backend/api_gateway/requirements.txt /app/

# Test if files are complete for build
RUN ls -la /app/
RUN test -f /app/main.py || (echo "main.py missing" && exit 1)

# Test requirements installation
RUN pip install -r requirements.txt

CMD ["echo", "Production build test successful"]

#!/bin/bash

set -e

SERVICE_NAME="chatbot_service"
IMAGE_NAME="${SERVICE_NAME}:debug"
ENV_FILE="backend/services/${SERVICE_NAME}/.env_template"
DOCKERFILE="backend/services/${SERVICE_NAME}/Dockerfile"
TARGET_STAGE="debug"
PORT=5003

echo "🚀 Building $IMAGE_NAME ..."
docker build --target $TARGET_STAGE -f $DOCKERFILE -t $IMAGE_NAME .

echo "✅ Build complete. Starting container..."

docker run --rm -it \
  --name ${SERVICE_NAME}_dev \
  --env-file $ENV_FILE \
  -p ${PORT}:${PORT} \
  $IMAGE_NAME


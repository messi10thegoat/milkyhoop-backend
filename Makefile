# Makefile MilkyHoop 🚀

.PHONY: build deploy clean test migrate

build:
	@echo "🔨 Building all services..."
	docker-compose -f docker/docker-compose.yml build

deploy:
	bash scripts/deploy.sh

migrate:
	bash scripts/migrate.sh

test:
	pytest tests/unit-tests && pytest tests/integration-tests

docker-clean:
	docker system prune -a --volumes -f

logs:
	docker-compose -f docker/docker-compose.yml logs -f

start:
	docker-compose -f docker/docker-compose.yml up -d

stop:
	docker-compose -f docker/docker-compose.yml down


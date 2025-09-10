SERVICE_NAME=notification-service
BINARY_NAME=main

build:
	go build -o bin/$(BINARY_NAME) ./cmd/server

run:
	go run ./cmd/server

proto:
	buf generate

tidy:
	go mod tidy



# 📦 Makefile for notification-service MilkyHoop
# Modular, CI/CD ready, and reusable for any Go service using SQLC

SQLC_BIN ?= sqlc
SQLC_CONFIG ?= sqlc.yaml

# 💻 Generate SQLC code
sqlc:
	@echo "🔧 Generating SQLC code from $(SQLC_CONFIG)..."
	@$(SQLC_BIN) generate -f $(SQLC_CONFIG)
	@echo "✅ SQLC generate complete."

# 🧪 Check if generated files exist
check-sqlc:
	@test -f internal/repo/db/queries.sql.go && echo "✅ queries.sql.go exists." || echo "❌ Missing queries.sql.go"
	@test -f internal/repo/db/models.go && echo "✅ models.go exists." || echo "❌ Missing models.go"

# 🧼 Clean generated code (optional)
clean-sqlc:
	@echo "🧹 Cleaning SQLC generated files..."
	@rm -f internal/repo/db/*.go
	@echo "✅ Clean complete."

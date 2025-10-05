package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/joho/godotenv"
	"github.com/milkyhoop/notification-service/internal/delivery"
	"github.com/milkyhoop/notification-service/internal/observability"
	"github.com/milkyhoop/notification-service/pkg/logger"
)

func main() {
	// Load .env file (lokal/dev)
	if err := godotenv.Load(); err != nil {
		fmt.Println("⚠️ Warning: .env file not loaded")
	}

	// Init structured logger
	logger.InitLogger()

	// Init Prometheus metrics
	observability.InitMetrics()

	// Start Prometheus metrics HTTP server (:8080)
	delivery.StartMetricsServer()

	// Create cancellable context
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Jalankan gRPC server
	go delivery.StartGRPCServer()

	// Jalankan Kafka consumer
	go delivery.StartKafkaConsumer(ctx)

	// Graceful shutdown
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	cancel()
}

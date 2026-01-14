package main

import (
	"os"
	"os/signal"
	"syscall"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"

	"milkyhoop/backend/services/visualhoop-compiler/internal/delivery"
	"milkyhoop/backend/services/visualhoop-compiler/internal/monitoring"
)

func main() {
	zerolog.TimeFieldFormat = zerolog.TimeFormatUnix
	log.Logger = log.Output(zerolog.ConsoleWriter{Out: os.Stderr})

	// 📈 Start Prometheus metrics server
	go monitoring.StartMetricsServer()

	// 🚀 Start gRPC compiler server
	go func() {
		port := os.Getenv("PORT")
		if port == "" {
			port = "5001"
		}

		if err := delivery.RunCompilerServer(port); err != nil {
			log.Fatal().Err(err).Msg("❌ Failed to run compiler server")
		}
	}()

	// 🛑 Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	<-sigChan
	log.Info().Msg("👋 Gracefully shutting down")
}

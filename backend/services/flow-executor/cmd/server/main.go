package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/milkyhoop/flow-executor/internal/delivery"
	"github.com/milkyhoop/flow-executor/internal/executor"
	"github.com/milkyhoop/flow-executor/internal/observer"
	"github.com/milkyhoop/flow-executor/internal/utils"
)

func main() {
	// Load .env dari root (optional, tidak error jika tidak ada)
	_ = godotenv.Load("../../../.env")

	// Inisialisasi logger zerolog
	utils.InitLogger("flow-executor")

	// Inisialisasi Kafka writer
	delivery.InitKafkaWriter()

	utils.Log.Info().Msg("🚀 Flow Executor MilkyHoop Started")

	// Register Prometheus metrics
	observer.RegisterMetrics()

	// HTTP server mux
	mux := http.NewServeMux()

	// Health check endpoint
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("OK"))
	})

	// Endpoint untuk menjalankan sample flow
	mux.HandleFunc("/run-sample", func(w http.ResponseWriter, r *http.Request) {
		err := executor.RunFlowFromFile("flows/examples/sample_flow.json")
		if err != nil {
			utils.Log.Error().Err(err).Msg("❌ Error running sample flow")
			http.Error(w, "❌ Error running flow: "+err.Error(), http.StatusInternalServerError)
			return
		}
		w.Write([]byte("✅ Flow execution completed."))
	})

	// Endpoint untuk menjalankan order menu flow
	mux.HandleFunc("/run-order-menu", func(w http.ResponseWriter, r *http.Request) {
		err := executor.RunFlowFromFile("flows/examples/order_menu.json")
		if err != nil {
			utils.Log.Error().Err(err).Msg("❌ Error running order_menu flow")
			http.Error(w, "❌ Error running flow: "+err.Error(), http.StatusInternalServerError)
			return
		}
		w.Write([]byte("✅ Flow order-menu executed."))
	})

	// Endpoint untuk menjalankan flow dari file .pb
	mux.HandleFunc("/run-from-pb", handleRunFromPB)

	// Endpoint baru untuk EKSEKUSI flow dari file dengan dukungan input POST
	mux.HandleFunc("/run-flow/", func(w http.ResponseWriter, r *http.Request) {
		filename := strings.TrimPrefix(r.URL.Path, "/run-flow/")
		fullpath := filepath.Join("flows/examples", filename)

		// Coba override jika file ada di flows/global/
		globalPath := filepath.Join("flows/global", filename)
		if _, err := os.Stat(globalPath); err == nil {
			fullpath = globalPath
		}

		// Parse input dari POST body (jika ada)
		var input map[string]interface{}
		if r.Method == http.MethodPost {
			if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
				utils.Log.Warn().Err(err).Msg("⚠️ Tidak bisa parse input JSON")
				input = map[string]interface{}{}
			}
		}

		utils.Log.Debug().Interface("input", input).Msg("🟡 Received Input")

		// ✅ FIX: Gunakan RunFlowAndReturnOutput untuk mendapatkan hasil
		result, err := executor.RunFlowAndReturnOutput(fullpath, input)
		if err != nil {
			utils.Log.Error().Err(err).Str("filename", filename).Msg("❌ Error running flow")
			http.Error(w, "❌ Error running flow: "+err.Error(), http.StatusInternalServerError)
			return
		}

		// ✅ FIX: Kirim hasil sebagai JSON response
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "success",
			"result": result,
		}); err != nil {
			utils.Log.Error().Err(err).Msg("❌ Error encoding JSON response")
			http.Error(w, "❌ Error encoding response", http.StatusInternalServerError)
			return
		}

		utils.Log.Info().
			Str("filename", filename).
			Str("fullpath", fullpath).
			Interface("result", result).
			Msg("✅ Flow executed successfully")
	})

	// Endpoint untuk Prometheus metrics
	mux.Handle("/metrics", promhttp.Handler())

	// Konfigurasi HTTP server dengan graceful shutdown
	server := &http.Server{
		Addr:    ":8088",
		Handler: mux,
	}

	// Channel untuk menangani shutdown
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)

	// Jalankan server di goroutine
	go func() {
		utils.Log.Info().Msg("🌐 HTTP server running on :8088")
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			utils.Log.Fatal().Err(err).Msg("❌ Server error")
		}
	}()

	// Tunggu sinyal shutdown
	<-stop
	utils.Log.Info().Msg("🛑 Shutdown signal received, stopping server...")

	// Graceful shutdown
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		utils.Log.Fatal().Err(err).Msg("❌ Server forced to shutdown")
	}

	utils.Log.Info().Msg("✅ Server gracefully stopped.")
}

func handleRunFromPB(w http.ResponseWriter, r *http.Request) {
	err := executor.RunProtobufFlowFromFile("flows/compiled/sample_flow.pb")
	if err != nil {
		utils.Log.Error().Err(err).Msg("❌ Failed to execute flow from .pb")
		http.Error(w, "❌ Flow execution failed: "+err.Error(), http.StatusInternalServerError)
		return
	}

	fmt.Fprintln(w, "✅ Flow from .pb executed successfully.")
}
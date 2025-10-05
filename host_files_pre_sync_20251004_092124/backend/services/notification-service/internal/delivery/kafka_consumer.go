package delivery

import (
	"context"
	"time"

	"github.com/milkyhoop/notification-service/internal/config"
	"github.com/milkyhoop/notification-service/internal/observability"
	"github.com/milkyhoop/notification-service/internal/service"
	"github.com/milkyhoop/notification-service/pkg/logger"
	"github.com/segmentio/kafka-go"
)

func StartKafkaConsumer(ctx context.Context) {
	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers: config.KafkaBrokers(),
		Topic:   config.KafkaTopic(),
		GroupID: config.KafkaGroupID(),
	})
	defer reader.Close()

	logger.Log.Info().
		Str("topic", config.KafkaTopic()).
		Msg("🔄 Listening to Kafka topic")

	for {
		select {
		case <-ctx.Done():
			logger.Log.Warn().Msg("🛑 Kafka consumer context cancelled")
			return
		default:
			handleKafkaMessage(ctx, reader)
		}
	}
}

func handleKafkaMessage(ctx context.Context, reader *kafka.Reader) {
	retryCount := 0
	for {
		m, err := reader.ReadMessage(ctx)
		if err != nil {
			logger.Log.Warn().
				Int("retry", retryCount+1).
				Err(err).
				Msg("⚠️ Kafka read error")
			retryCount++
			if retryCount >= 5 {
				logger.Log.Error().Msg("🚨 Max retries exceeded")
				return
			}
			time.Sleep(time.Duration(retryCount*500) * time.Millisecond)
			continue
		}

		ctxWithIDs := logger.InjectIDs(ctx)

		observability.KafkaMessagesConsumed.
			WithLabelValues(config.KafkaTopic()).
			Inc()

		logger.WithContext(ctxWithIDs).
			Str("payload", string(m.Value)).
			Msg("📨 Kafka received")

		// 🧠 Proses payload secara modular
		if err := service.HandleNotification(m.Value); err != nil {
			logger.WithContext(ctxWithIDs).
				Err(err).
				Msg("❌ Failed to process notification")
		}

		return
	}
}

package delivery

import (
	"context"
	"log"
	"os"

	"github.com/segmentio/kafka-go"
)

var kafkaWriter *kafka.Writer

// InitKafkaWriter inisialisasi writer Kafka (dipanggil saat startup)
func InitKafkaWriter() {
	brokers := os.Getenv("KAFKA_BROKER") // contoh: "localhost:9092"
	if brokers == "" {
		log.Println("⚠️ KAFKA_BROKER tidak diset, Kafka writer tidak aktif")
		return
	}

	kafkaWriter = kafka.NewWriter(kafka.WriterConfig{
		Brokers:  []string{brokers},
		Topic:    "send-notification",
		Balancer: &kafka.LeastBytes{},
	})

	log.Printf("📡 Kafka writer siap → topic: send-notification, broker: %s\n", brokers)
}

// PublishNotification mengirim payload notifikasi ke Kafka
func PublishNotification(payload []byte) error {
	if kafkaWriter == nil {
		return nil // Kafka tidak aktif, skip (bisa di-log)
	}

	err := kafkaWriter.WriteMessages(context.Background(),
		kafka.Message{
			Value: payload,
		},
	)
	if err != nil {
		log.Printf("❌ Gagal kirim ke Kafka: %v", err)
		return err
	}

	log.Printf("📤 Payload dikirim ke Kafka: %s", string(payload))
	return nil
}

package config

import (
	"os"
	"strings"
)

// ✅ Default broker (kafka service di Docker Compose)
func KafkaBrokers() []string {
	brokers := os.Getenv("KAFKA_BROKERS")
	if brokers == "" {
		brokers = "kafka:9092"
	}
	return strings.Split(brokers, ",")
}

// ✅ PATCH: Topik disamakan dengan flow-executor
func KafkaTopic() string {
	topic := os.Getenv("KAFKA_TOPIC")
	if topic == "" {
		topic = "send-notification" // ✅ PATCH
	}
	return topic
}


func KafkaGroupID() string {
	groupID := os.Getenv("KAFKA_GROUP_ID")
	if groupID == "" {
		groupID = "notification-service"
	}
	return groupID
}

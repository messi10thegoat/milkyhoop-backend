package delivery

import (
	"context"
	"encoding/json"
	"fmt"
)

// DummyShowMenu is a mock function simulating menu retrieval
func DummyShowMenu(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	fmt.Println("🍽 DummyShowMenu called.")
	return map[string]interface{}{
		"menu_id":   "coffee-1",
		"menu_name": "Kopi Susu Gula Aren",
		"price":     25000,
	}, nil
}

// DummyCreateOrder is a mock function simulating order creation
func DummyCreateOrder(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	fmt.Printf("🧾 DummyCreateOrder called with input: %+v\n", input)
	orderID := "order-" + input["menu_id"].(string)

	return map[string]interface{}{
		"order_id": orderID,
		"status":   "created",
	}, nil
}

// DummySendNotification is a mock function simulating notification send
func DummySendNotification(ctx context.Context, input map[string]interface{}) (map[string]interface{}, error) {
	fmt.Printf("📩 DummySendNotification called with input: %+v\n", input)

	payload, err := json.Marshal(input)
	if err != nil {
		return nil, fmt.Errorf("marshal failed: %w", err)
	}

	if err := PublishNotification(payload); err != nil {
		return nil, fmt.Errorf("kafka publish failed: %w", err)
	}

	return map[string]interface{}{
		"notified": true,
	}, nil
}

-- name: InsertNotification :one
INSERT INTO notifications (user_id, message, type, status, created_at)
VALUES ($1, $2, $3, $4, NOW())
RETURNING id;

-- name: ListNotificationsByUser :many
SELECT id, user_id, message, type, status, created_at
FROM notifications
WHERE user_id = $1
ORDER BY created_at DESC
LIMIT 100;

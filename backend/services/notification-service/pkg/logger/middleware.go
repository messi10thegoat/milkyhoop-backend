package logger

import (
	"context"
	"github.com/google/uuid"
)

type ctxKey string

const (
	TraceIDKey   ctxKey = "trace_id"
	RequestIDKey ctxKey = "request_id"
)

func InjectIDs(ctx context.Context) context.Context {
	traceID := uuid.New().String()
	requestID := uuid.New().String()
	ctx = context.WithValue(ctx, TraceIDKey, traceID)
	ctx = context.WithValue(ctx, RequestIDKey, requestID)
	return ctx
}

func GetTraceID(ctx context.Context) string {
	if v, ok := ctx.Value(TraceIDKey).(string); ok {
		return v
	}
	return ""
}

func GetRequestID(ctx context.Context) string {
	if v, ok := ctx.Value(RequestIDKey).(string); ok {
		return v
	}
	return ""
}

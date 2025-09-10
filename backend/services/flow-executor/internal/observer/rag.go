package observer

import (
	"github.com/milkyhoop/flow-executor/internal/ragclient"
)

// Actual RAG LLM query
func QueryRAGLLM(query string, tenantID string) (string, error) {
	return ragclient.QueryRAG(query, tenantID)
}
package executor

import "fmt"

type FlowContext struct {
	UserID    string                 `json:"user_id"`
	TenantID  string                 `json:"tenant_id"`
	Input     map[string]interface{} `json:"input"`               // ✅ Untuk inject input user
	Outputs   map[string]interface{} `json:"outputs,omitempty"`   // ✅ Output antar node (untuk template seperti {{fetch_answer.answer}})
	SessionID string                 `json:"session_id,omitempty"` // optional, untuk trace
}

type Node struct {
	ID         string                 `json:"id"`
	Hoop       string                 `json:"hoop"`
	Input      map[string]interface{} `json:"input,omitempty"`      // legacy, deprecated
	Parameters map[string]interface{} `json:"parameters,omitempty"` // ✅ utama dipakai sekarang
	InputFrom  string                 `json:"input_from,omitempty"`
	TruePath   string                 `json:"true_path,omitempty"`
	FalsePath  string                 `json:"false_path,omitempty"`
	JumpTo     string                 `json:"jump_to,omitempty"`
}

type FlowSpec struct {
	FlowID    string      `json:"flow_id"`
	TriggerID string      `json:"trigger_id"`
	Context   FlowContext `json:"context"`
	Nodes     []Node      `json:"nodes"`
}

// Type alias agar bisa dipanggil dari main.go
type Flow = FlowSpec

// ✅ Patch final agar input + outputs bisa dirender via template
func (f FlowSpec) ContextToMap() map[string]interface{} {
	fmt.Printf("DEBUG ContextToMap - TenantID value: '%s'\n", f.Context.TenantID)
	fmt.Printf("DEBUG ContextToMap - UserID value: '%s'\n", f.Context.UserID)
	
	context := map[string]interface{}{
		"user_id":    f.Context.UserID,
		"tenant_id":  f.Context.TenantID,
		"session_id": f.Context.SessionID,
	}
	
	// Flatten input content directly to root context
	for key, value := range f.Context.Input {
		context[key] = value
	}
	
	// Inject outputs sebagai key langsung ke context map
	for nodeID, output := range f.Context.Outputs {
		context[nodeID] = output
	}
	
	fmt.Printf("DEBUG ContextToMap - Final context tenant_id: '%v'\n", context["tenant_id"])
	return context
}
package executor

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/milkyhoop/flow-executor/internal/loader"
	"github.com/milkyhoop/flow-executor/internal/observer"
	"github.com/milkyhoop/flow-executor/internal/utils"
	flowpb "github.com/milkyhoop/flow-executor/internal/proto/flow"

	"google.golang.org/protobuf/proto"
)

func RunFlowFromFileWithInput(path string, input map[string]interface{}) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("failed to read flow file: %w", err)
	}

	var flow FlowSpec
	if err := json.Unmarshal(data, &flow); err != nil {
		return fmt.Errorf("failed to parse flow JSON: %w", err)
	}

	if flow.Context.Input == nil {
		flow.Context.Input = make(map[string]interface{})
	}
	

	for k, v := range input {
		flow.Context.Input[k] = v
	}


	// Check nested input structure
	if inputMap, ok := input["input"].(map[string]interface{}); ok {
		if tenant, ok := inputMap["tenant_id"].(string); ok {
			flow.Context.TenantID = tenant
		}
		if user, ok := inputMap["user_id"].(string); ok {
			flow.Context.UserID = user
		}
	}

	return RunFlow(flow)
}

func RunFlowFromFile(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("failed to read flow file: %w", err)
	}

	var flow FlowSpec
	if err := json.Unmarshal(data, &flow); err != nil {
		return fmt.Errorf("failed to parse flow JSON: %w", err)
	}

	return RunFlow(flow)
}

func RunProtobufFlowFromFile(path string) error {
	_, file := filepath.Split(path)
	jsonPath := file[:len(file)-3] + "json"
	pbPath := path

	err := loader.CompileJSON(jsonPath, pbPath)
	if err != nil {
		return fmt.Errorf("failed to compile JSON to .pb: %w", err)
	}

	utils.Log.Info().
		Str("json_path", jsonPath).
		Str("pb_path", pbPath).
		Msg("✅ JSON compiled to .pb via Visualhoop-compiler")

	data, err := os.ReadFile(pbPath)
	if err != nil {
		return fmt.Errorf("failed to read protobuf file: %w", err)
	}

	var protoFlow flowpb.Flow
	if err := proto.Unmarshal(data, &protoFlow); err != nil {
		return fmt.Errorf("failed to unmarshal .pb: %w", err)
	}

	var nodes []Node
	for _, pn := range protoFlow.Nodes {
		nodes = append(nodes, Node{
			ID:        pn.Id,
			Hoop:      pn.Hoop,
			InputFrom: pn.InputFrom,
		})
	}

	flow := FlowSpec{
		FlowID:    protoFlow.Id,
		TriggerID: "exec-pb",
		Context: FlowContext{
			UserID:   "dummy-user",
			TenantID: "dummy-tenant",
		},
		Nodes: nodes,
	}

	return RunFlow(flow)
}

func RunFlow(flow FlowSpec) error {
	utils.Log.Info().Str("flow_id", flow.FlowID).Msg("🚀 Running Flow")
	if flow.Context.Outputs == nil { flow.Context.Outputs = make(map[string]interface{}) }
	outputs := make(map[string]map[string]interface{})
	nodeMap := make(map[string]Node)

	// ✅ PATCH: Inisialisasi Outputs dengan tipe yang benar
	if flow.Context.Outputs == nil {
		flow.Context.Outputs = make(map[string]interface{})
	}

	for _, n := range flow.Nodes {
		nodeMap[n.ID] = n
	}

	if len(flow.Nodes) == 0 {
		return fmt.Errorf("❌ Flow '%s' tidak memiliki node", flow.FlowID)
	}

	currentID := flow.Nodes[0].ID
	status := "success"

	for {
		node, ok := nodeMap[currentID]
		if !ok {
			break
		}

		if node.Hoop == "" {
			currentID = getNextNodeID(flow.Nodes, node.ID)
			continue
		}

		utils.Log.Info().
			Str("node_id", node.ID).
			Str("hoop", node.Hoop).
			Msg("🔧 Executing Node")

		var rawInput map[string]interface{}
		if node.InputFrom != "" {
			ref, ok := outputs[node.InputFrom]
			if !ok {
				status = "fail"
				observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
				return fmt.Errorf("node %s: missing input from %s", node.ID, node.InputFrom)
			}
			rawInput = ref
		} else {
			rawInput = node.Parameters
		}

		contextMap := flow.ContextToMap()
		utils.Log.Debug().Interface("context_map", contextMap).Msg("🧵 Context map (sebelum render)")
		utils.Log.Debug().Interface("context_map", contextMap).Msg("🧩 Merged context + input")

		input := RenderTemplate(rawInput, contextMap)
		utils.Log.Debug().Interface("rendered_input", input).Msg("🧪 Rendered Input")

		if node.Hoop == "IfNode" {
			nextID, err := ExecuteIfNode(flow, node, input, outputs)
			if err != nil {
				status = "fail"
				observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
				return err
			}
			currentID = nextID
			continue
		}

		output, nextID, err := ExecuteNode(flow, node, input)
		if err != nil {
			status = "fail"
			observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
			return err
		}

		// ✅ PATCH: assignment tanpa panic
		outputs[node.ID] = output
		flow.Context.Outputs[node.ID] = output

		event := map[string]interface{}{
			"flow_id":   flow.FlowID,
			"node_id":   node.ID,
			"hoop":      node.Hoop,
			"input":     input,
			"output":    output,
			"user_id":   flow.Context.UserID,
			"tenant_id": flow.Context.TenantID,
		}
		if b, err := json.Marshal(event); err == nil {
			observer.PublishNotification(flow.Context.UserID, string(b))
		}

		if nextID != "" {
			currentID = nextID
		} else {
			currentID = getNextNodeID(flow.Nodes, node.ID)
			if currentID == "" {
				break
			}
		}
	}

	observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
	utils.Log.Info().Msg("✅ Flow completed successfully.")
	return nil
}


func RunFlowAndReturnOutput(path string, input map[string]interface{}) (map[string]interface{}, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read flow file: %w", err)
	}


	var flow FlowSpec
	if err := json.Unmarshal(data, &flow); err != nil {
		return nil, fmt.Errorf("failed to parse flow JSON: %w", err)
	}

	if flow.Context.Input == nil {
		flow.Context.Input = make(map[string]interface{})
	}
	for k, v := range input {
		flow.Context.Input[k] = v
	}

	// Check nested input structure
	if inputMap, ok := input["input"].(map[string]interface{}); ok {
		if tenant, ok := inputMap["tenant_id"].(string); ok {
			flow.Context.TenantID = tenant
		}
		if user, ok := inputMap["user_id"].(string); ok {
			flow.Context.UserID = user
		}
	}






	utils.Log.Info().Str("flow_id", flow.FlowID).Msg("🚀 Running Flow")
	if flow.Context.Outputs == nil { flow.Context.Outputs = make(map[string]interface{}) }
	outputs := make(map[string]map[string]interface{})
	nodeMap := make(map[string]Node)
	for _, n := range flow.Nodes {
		nodeMap[n.ID] = n
	}

	if len(flow.Nodes) == 0 {
		return nil, fmt.Errorf("❌ Flow '%s' tidak memiliki node", flow.FlowID)
	}

	currentID := flow.Nodes[0].ID
	var lastOutput map[string]interface{}
	outputs = make(map[string]map[string]interface{})
	status := "success"

	for {
		node, ok := nodeMap[currentID]
		if !ok {
			break
		}

		if node.Hoop == "" {
			currentID = getNextNodeID(flow.Nodes, node.ID)
			continue
		}

		utils.Log.Info().
			Str("node_id", node.ID).
			Str("hoop", node.Hoop).
			Msg("🔧 Executing Node")

		var rawInput map[string]interface{}
		if node.InputFrom != "" {
			ref, ok := outputs[node.InputFrom]
			if !ok {
				status = "fail"
				observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
				return nil, fmt.Errorf("node %s: missing input from %s", node.ID, node.InputFrom)
			}
			rawInput = ref
		} else {
			rawInput = node.Parameters
		}

		contextMap := flow.ContextToMap()
		input := RenderTemplate(rawInput, contextMap)

		if node.Hoop == "IfNode" {
			nextID, err := ExecuteIfNode(flow, node, input, outputs)
			if err != nil {
				status = "fail"
				observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
				return nil, err
			}
			currentID = nextID
			continue
		}

		output, nextID, err := ExecuteNode(flow, node, input)
		if err != nil {
			status = "fail"
			observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
			return nil, err
		}

		lastOutput = output
		outputs[node.ID] = output 
		flow.Context.Outputs[node.ID] = output


		if b, err := json.Marshal(map[string]interface{}{
			"flow_id": flow.FlowID, "node_id": node.ID, "hoop": node.Hoop,
			"input": input, "output": output,
			"user_id": flow.Context.UserID, "tenant_id": flow.Context.TenantID,
		}); err == nil {
			observer.PublishNotification(flow.Context.UserID, string(b))
		}

		if nextID != "" {
			currentID = nextID
		} else {
			currentID = getNextNodeID(flow.Nodes, node.ID)
			if currentID == "" {
				break
			}
		}
	}

	observer.FlowExecutionCount.WithLabelValues(flow.FlowID, status).Inc()
	utils.Log.Info().Msg("✅ Flow completed successfully.")


	utils.Log.Debug().Interface("outputs", outputs).Msg("🔍 All outputs before final return")

	if len(lastOutput) == 0 {
		if output, ok := outputs["fetch_answer"]; ok {
			return output, nil
		}
	}
	utils.Log.Info().Interface("lastOutput", lastOutput).Msg("🐛 Last output before return")
	return lastOutput, nil


}

func getNextNodeID(nodes []Node, currentID string) string {
	for i, n := range nodes {
		if n.ID == currentID && i+1 < len(nodes) {
			return nodes[i+1].ID
		}
	}
	return ""
}

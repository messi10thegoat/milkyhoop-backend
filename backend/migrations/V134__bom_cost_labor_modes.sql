-- V134: calculate_bom_cost — include piece_rate + subcontract in labor_cost and total_cost
CREATE OR REPLACE FUNCTION calculate_bom_cost(p_bom_id uuid) RETURNS bigint AS $$
DECLARE
    v_material_cost BIGINT;
    v_labor_cost BIGINT;
    v_overhead_cost BIGINT;
    v_subcontract_cost BIGINT;
BEGIN
    SELECT COALESCE(SUM(extended_cost), 0)
    INTO v_material_cost
    FROM bom_components
    WHERE bom_id = p_bom_id;

    -- Labor cost by mode:
    --   time_based: (setup+run) * labor_rate / 60
    --   piece_rate: cost_per_piece (per BOM output unit)
    --   subcontract: handled separately (surfaced via subcontract_cost)
    --   none: 0
    SELECT
        COALESCE(SUM(
            CASE labor_mode
                WHEN 'time_based' THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * labor_rate_per_hour / 60
                WHEN 'piece_rate' THEN COALESCE(cost_per_piece, 0)
                ELSE 0
            END
        ), 0),
        COALESCE(SUM(
            CASE labor_mode
                WHEN 'time_based' THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * overhead_rate_per_hour / 60
                ELSE 0
            END
        ), 0),
        COALESCE(SUM(
            CASE labor_mode
                WHEN 'subcontract' THEN COALESCE(subcontract_cost_per_unit, 0)
                ELSE 0
            END
        ), 0)
    INTO v_labor_cost, v_overhead_cost, v_subcontract_cost
    FROM bom_operations
    WHERE bom_id = p_bom_id;

    UPDATE bill_of_materials
    SET standard_cost = v_material_cost,
        labor_cost = v_labor_cost,
        overhead_cost = v_overhead_cost,
        total_cost = v_material_cost + v_labor_cost + v_overhead_cost + v_subcontract_cost,
        updated_at = NOW()
    WHERE id = p_bom_id;

    RETURN v_material_cost + v_labor_cost + v_overhead_cost + v_subcontract_cost;
END;
$$ LANGUAGE plpgsql;

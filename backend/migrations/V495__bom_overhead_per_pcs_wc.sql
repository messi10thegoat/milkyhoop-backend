-- V495: BUG-180 (12 Nov 2026) -- operasi BOM "per pcs (internal)" yang punya Work Center ikut menanggung OVERHEAD = menit/60 x tarif overhead WC (paritas Accurate:
-- upah borongan + overhead). Upah per pcs tetap cost_per_piece (tak ditambah tarif labor jam); per jam TIDAK berubah; per pcs TANPA Work Center = overhead 0.
-- DISETUJUI LANGSUNG pemilik (12 Nov, via MASTER): migrasi calculate_bom_cost DIIZINKAN. Hanya CREATE OR REPLACE fungsi: nol baris data berubah; BOM lama TIDAK berubah sendiri
-- (biaya tersimpan; baru berubah saat BOM DRAF dihitung ulang -- recalculate/pratinjau BUG-179; BOM aktif tak disentuh). Pembalik: V495__bom_overhead_per_pcs_wc_ROLLBACK.sql (kembali ke V134).
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

    -- Labor: time_based = (setup+run) * labor_rate / 60 | piece_rate = cost_per_piece | subcontract = surfaced via subcontract_cost | none = 0
    -- Overhead: time_based = (setup+run) * overhead_rate / 60 | piece_rate BER-Work Center = (setup+run) * overhead_rate / 60 (BUG-180) | selain itu 0
    SELECT
        COALESCE(SUM(
            CASE labor_mode
                WHEN 'time_based' THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * labor_rate_per_hour / 60
                WHEN 'piece_rate' THEN COALESCE(cost_per_piece, 0)
                ELSE 0
            END
        ), 0),
        COALESCE(SUM(
            CASE
                WHEN labor_mode = 'time_based' THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * overhead_rate_per_hour / 60
                WHEN labor_mode = 'piece_rate' AND work_center_id IS NOT NULL THEN (COALESCE(setup_time_minutes,0) + COALESCE(run_time_minutes,0)) * overhead_rate_per_hour / 60
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

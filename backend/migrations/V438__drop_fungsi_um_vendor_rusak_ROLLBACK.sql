-- ROLLBACK V438: pulihkan kelima fungsi PERSIS seperti di produksi 22 Okt (pg_get_functiondef), termasuk cacatnya.
CREATE OR REPLACE FUNCTION public.get_available_vendor_deposits(p_vendor_id uuid)
 RETURNS TABLE(id uuid, deposit_number character varying, deposit_date date, amount bigint, applied_amount bigint, remaining_amount bigint, reference character varying)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        vd.id,
        vd.deposit_number,
        vd.deposit_date,
        vd.amount,
        vd.applied_amount,
        vd.remaining_amount,
        vd.reference
    FROM vendor_deposits vd
    WHERE vd.vendor_id = p_vendor_id
    AND vd.status IN ('posted', 'partial')
    AND vd.remaining_amount > 0
    ORDER BY vd.deposit_date ASC;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_vendor_deposit_applications(p_deposit_id uuid)
 RETURNS TABLE(id uuid, bill_id uuid, bill_number character varying, bill_date date, applied_amount bigint, applied_date date)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        vda.id,
        vda.bill_id,
        b.bill_number,
        b.bill_date,
        vda.amount as applied_amount,
        vda.applied_date
    FROM vendor_deposit_applications vda
    JOIN bills b ON vda.bill_id = b.id
    WHERE vda.vendor_deposit_id = p_deposit_id
    ORDER BY vda.applied_date DESC;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_vendor_deposit_refunds(p_deposit_id uuid)
 RETURNS TABLE(id uuid, refund_date date, amount bigint, reference character varying, notes text)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        vdr.id,
        vdr.refund_date,
        vdr.amount,
        vdr.reference,
        vdr.notes
    FROM vendor_deposit_refunds vdr
    WHERE vdr.vendor_deposit_id = p_deposit_id
    ORDER BY vdr.refund_date DESC;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_vendor_deposit_summary(p_tenant_id text)
 RETURNS TABLE(total_deposits bigint, total_applied bigint, total_remaining bigint, deposit_count integer, pending_count integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(amount), 0)::BIGINT as total_deposits,
        COALESCE(SUM(applied_amount), 0)::BIGINT as total_applied,
        COALESCE(SUM(remaining_amount), 0)::BIGINT as total_remaining,
        COUNT(*)::INTEGER as deposit_count,
        COUNT(CASE WHEN status IN ('posted', 'partial') AND remaining_amount > 0 THEN 1 END)::INTEGER as pending_count
    FROM vendor_deposits
    WHERE tenant_id = p_tenant_id
    AND status != 'void';
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_vendor_deposits(p_vendor_id uuid, p_status character varying DEFAULT NULL::character varying)
 RETURNS TABLE(id uuid, deposit_number character varying, deposit_date date, amount bigint, applied_amount bigint, remaining_amount bigint, status character varying, reference character varying, purchase_order_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        vd.id,
        vd.deposit_number,
        vd.deposit_date,
        vd.amount,
        vd.applied_amount,
        vd.remaining_amount,
        vd.status,
        vd.reference,
        vd.purchase_order_id
    FROM vendor_deposits vd
    WHERE vd.vendor_id = p_vendor_id
    AND (p_status IS NULL OR vd.status = p_status)
    ORDER BY vd.deposit_date DESC;
END;
$function$
;

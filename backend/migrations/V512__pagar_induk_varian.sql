-- V512: PAGAR INDUK VARIAN DI DOKUMEN (putusan Q1 26 Nov; GO pemilik LANGSUNG 27 Nov ±07:00 via MASTER). Usulan: rantai/USULAN-V511-pagar-induk-varian-20261127.sql
-- Barang INDUK varian (products.is_matrix_parent) tak boleh di baris dokumen; transaksi selalu di varian (pola Accurate).
-- Satu trigger di 15 tabel baris dokumen (tak ada validator baris bersama di ~15 penulis). Prod 27 Nov: 0 barang matriks -> nol data lama tertolak.
-- Galat: SQLSTATE MH422 -> main.py memetakan ke 422 VARIAN_INDUK_DI_DOKUMEN. Pembalik: V512__pagar_induk_varian_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig).
CREATE OR REPLACE FUNCTION tolak_induk_varian_di_dokumen() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_nama text;
BEGIN
  v_id := NULLIF(to_jsonb(NEW) ->> TG_ARGV[0], '')::uuid;
  IF v_id IS NULL THEN RETURN NEW; END IF;
  SELECT nama_produk INTO v_nama FROM products WHERE id = v_id AND is_matrix_parent;
  IF FOUND THEN
    RAISE EXCEPTION 'VARIAN_INDUK_DI_DOKUMEN: "%" adalah barang induk varian; pilih variannya (mis. ukuran/warna).', v_nama
      USING ERRCODE = 'MH422';
  END IF;
  RETURN NEW;
END $$;

DO $$
DECLARE t text[];
BEGIN
  FOREACH t SLICE 1 IN ARRAY ARRAY[
    ['quote_items','item_id'], ['sales_order_items','item_id'], ['sales_invoice_items','item_id'], ['sales_receipt_items','item_id'],
    ['credit_note_items','item_id'], ['invoice_fulfillment_items','product_id'],
    ['purchase_order_items','item_id'], ['goods_receipt_items','item_id'], ['bill_items','product_id'], ['vendor_credit_items','item_id'],
    ['recurring_bill_items','item_id'],
    ['stock_adjustment_items','product_id'], ['stock_transfer_items','item_id'], ['stock_opname_lines','product_id'],
    ['branch_transfer_lines','product_id']]
  LOOP
    EXECUTE format('CREATE TRIGGER trg_tolak_induk_varian BEFORE INSERT OR UPDATE OF %I ON %I FOR EACH ROW '
                   'EXECUTE FUNCTION tolak_induk_varian_di_dokumen(%L)', t[2], t[1], t[2]);
  END LOOP;
END $$;

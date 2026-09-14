-- ROLLBACK V257
DROP TRIGGER IF EXISTS trg_products_kategori_register ON products;
DROP FUNCTION IF EXISTS register_item_category();
DROP TABLE IF EXISTS item_categories;

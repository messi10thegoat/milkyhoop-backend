                                                                                           pg_get_viewdef                                                                                           
----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  SELECT transaksi_harian.tenant_id,                                                                                                                                                               +
     date_trunc('month'::text, to_timestamp((transaksi_harian."timestamp" / 1000)::double precision)) AS periode,                                                                                  +
     sum(                                                                                                                                                                                          +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'penjualan'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'total_nominal'::text)::bigint+
             ELSE 0::bigint                                                                                                                                                                        +
         END) AS pendapatan,                                                                                                                                                                       +
     sum(                                                                                                                                                                                          +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'pembelian'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'total_nominal'::text)::bigint+
             ELSE 0::bigint                                                                                                                                                                        +
         END) AS pembelian,                                                                                                                                                                        +
     sum(                                                                                                                                                                                          +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'beban'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'nominal'::text)::bigint          +
             ELSE 0::bigint                                                                                                                                                                        +
         END) AS beban,                                                                                                                                                                            +
     sum(                                                                                                                                                                                          +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'penjualan'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'total_nominal'::text)::bigint+
             ELSE 0::bigint                                                                                                                                                                        +
         END) - sum(                                                                                                                                                                               +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'pembelian'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'total_nominal'::text)::bigint+
             ELSE 0::bigint                                                                                                                                                                        +
         END) AS laba_kotor,                                                                                                                                                                       +
     sum(                                                                                                                                                                                          +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'penjualan'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'total_nominal'::text)::bigint+
             ELSE 0::bigint                                                                                                                                                                        +
         END) - sum(                                                                                                                                                                               +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'pembelian'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'total_nominal'::text)::bigint+
             ELSE 0::bigint                                                                                                                                                                        +
         END) - sum(                                                                                                                                                                               +
         CASE                                                                                                                                                                                      +
             WHEN transaksi_harian.jenis_transaksi::text = 'beban'::text AND transaksi_harian.status::text = 'approved'::text THEN (transaksi_harian.payload ->> 'nominal'::text)::bigint          +
             ELSE 0::bigint                                                                                                                                                                        +
         END) AS laba_bersih,                                                                                                                                                                      +
     count(*) FILTER (WHERE transaksi_harian.jenis_transaksi::text = 'penjualan'::text AND transaksi_harian.status::text = 'approved'::text) AS jumlah_penjualan,                                  +
     count(*) FILTER (WHERE transaksi_harian.jenis_transaksi::text = 'pembelian'::text AND transaksi_harian.status::text = 'approved'::text) AS jumlah_pembelian,                                  +
     count(*) FILTER (WHERE transaksi_harian.jenis_transaksi::text = 'beban'::text AND transaksi_harian.status::text = 'approved'::text) AS jumlah_beban,                                          +
     max(transaksi_harian.updated_at) AS last_updated                                                                                                                                              +
    FROM transaksi_harian                                                                                                                                                                          +
   WHERE transaksi_harian.status::text = 'approved'::text                                                                                                                                          +
   GROUP BY transaksi_harian.tenant_id, (date_trunc('month'::text, to_timestamp((transaksi_harian."timestamp" / 1000)::double precision)));
(1 row)


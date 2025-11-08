-- CreateEnum
CREATE TYPE "Role" AS ENUM ('FREE', 'BUSINESS', 'PRO', 'CORPORATE', 'ADMIN');

-- CreateEnum
CREATE TYPE "MediaType" AS ENUM ('IMAGE', 'VIDEO');

-- CreateTable
CREATE TABLE "User" (
    "id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "tenantId" TEXT,
    "username" TEXT,
    "name" TEXT,
    "fullname" TEXT,
    "nickname" TEXT,
    "avatarUrl" TEXT,
    "coverPhotoUrl" TEXT,
    "bio" TEXT,
    "emailVerified" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "isVerified" BOOLEAN NOT NULL DEFAULT false,
    "role" "Role" NOT NULL DEFAULT 'FREE',
    "lastInteraction" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "passwordHash" TEXT,
    "oauthProvider" TEXT,
    "oauthId" TEXT,

    CONSTRAINT "User_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Account" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "providerAccountId" TEXT NOT NULL,
    "access_token" TEXT,
    "expires_at" INTEGER,
    "id_token" TEXT,
    "refresh_token" TEXT,
    "scope" TEXT,
    "session_state" TEXT,
    "token_type" TEXT,

    CONSTRAINT "Account_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Session" (
    "id" TEXT NOT NULL,
    "sessionToken" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "expires" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Session_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "UserSecurity" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "passwordHash" TEXT,
    "twoFactorEnabled" BOOLEAN NOT NULL DEFAULT false,
    "oauthId" TEXT,
    "oauthProvider" TEXT,

    CONSTRAINT "UserSecurity_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "UserProfile" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "bio" TEXT,
    "phoneNumber" TEXT,
    "tagline" TEXT,
    "publicUrlSlug" TEXT,
    "digitalSignature" TEXT,

    CONSTRAINT "UserProfile_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "VerificationToken" (
    "identifier" TEXT NOT NULL,
    "token" TEXT NOT NULL,
    "expires" TIMESTAMP(3) NOT NULL
);

-- CreateTable
CREATE TABLE "messages" (
    "id" SERIAL NOT NULL,
    "user_id" VARCHAR(255) NOT NULL,
    "message" TEXT NOT NULL,
    "created_at" TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "messages_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "UserBusiness" (
    "businessId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "businessName" TEXT NOT NULL,
    "businessCategory" TEXT NOT NULL,
    "businessLicense" TEXT NOT NULL,
    "taxId" TEXT NOT NULL,
    "businessWebsite" TEXT,

    CONSTRAINT "UserBusiness_pkey" PRIMARY KEY ("businessId")
);

-- CreateTable
CREATE TABLE "UserLocations" (
    "locationId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "latitude" DOUBLE PRECISION NOT NULL,
    "longitude" DOUBLE PRECISION NOT NULL,
    "addressDetail" TEXT NOT NULL,
    "isPrimary" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "UserLocations_pkey" PRIMARY KEY ("locationId")
);

-- CreateTable
CREATE TABLE "UserFinance" (
    "financeId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "balance" DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    "currency" TEXT NOT NULL,
    "paymentMethods" JSONB NOT NULL,
    "loyaltyPoints" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "UserFinance_pkey" PRIMARY KEY ("financeId")
);

-- CreateTable
CREATE TABLE "UserSubscriptions" (
    "subscriptionId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "planId" TEXT NOT NULL,
    "tokenUsage" INTEGER NOT NULL DEFAULT 0,
    "tokenLimit" INTEGER NOT NULL,
    "tokenResetAt" TIMESTAMP(3) NOT NULL,
    "subscriptionStart" TIMESTAMP(3) NOT NULL,
    "subscriptionEnd" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "UserSubscriptions_pkey" PRIMARY KEY ("subscriptionId")
);

-- CreateTable
CREATE TABLE "Plans" (
    "planId" TEXT NOT NULL,
    "planName" TEXT NOT NULL,

    CONSTRAINT "Plans_pkey" PRIMARY KEY ("planId")
);

-- CreateTable
CREATE TABLE "UserAISettings" (
    "aiSettingsId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "botSlug" TEXT NOT NULL,
    "ragEnabled" BOOLEAN NOT NULL DEFAULT false,
    "aiPersonalityProfile" JSONB NOT NULL,

    CONSTRAINT "UserAISettings_pkey" PRIMARY KEY ("aiSettingsId")
);

-- CreateTable
CREATE TABLE "UserMedia" (
    "mediaId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "mediaType" "MediaType" NOT NULL,
    "mediaUrl" TEXT NOT NULL,
    "uploadDate" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "UserMedia_pkey" PRIMARY KEY ("mediaId")
);

-- CreateTable
CREATE TABLE "Tenant" (
    "id" TEXT NOT NULL,
    "alias" TEXT NOT NULL,
    "display_name" TEXT NOT NULL,
    "menu_items" JSONB NOT NULL,
    "address" TEXT,
    "status" TEXT NOT NULL DEFAULT 'ACTIVE',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Tenant_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Order" (
    "id" TEXT NOT NULL,
    "customer_name" TEXT NOT NULL,
    "items" TEXT NOT NULL,
    "total_price" DOUBLE PRECISION NOT NULL,
    "status" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Order_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Memory" (
    "id" SERIAL NOT NULL,
    "title" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Memory_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RagDocument" (
    "id" SERIAL NOT NULL,
    "tenantId" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "source" TEXT,
    "tags" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "embeddings" JSONB DEFAULT '[]',

    CONSTRAINT "RagDocument_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "audit_logs" (
    "id" TEXT NOT NULL,
    "userId" TEXT,
    "eventType" TEXT NOT NULL,
    "ipAddress" TEXT,
    "userAgent" TEXT,
    "metadata" JSONB,
    "success" BOOLEAN NOT NULL DEFAULT true,
    "errorMessage" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "transaksi_harian" (
    "id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "created_by" TEXT NOT NULL,
    "actor_role" VARCHAR(50) NOT NULL,
    "timestamp" BIGINT NOT NULL,
    "jenis_transaksi" VARCHAR(50) NOT NULL,
    "payload" JSONB NOT NULL,
    "raw_text" TEXT,
    "raw_nlu" BYTEA,
    "metadata" JSONB,
    "receipt_url" TEXT,
    "receipt_checksum" VARCHAR(64),
    "idempotency_key" VARCHAR(255),
    "status" VARCHAR(50) NOT NULL DEFAULT 'draft',
    "approved_by" TEXT,
    "approved_at" BIGINT,
    "rekening_id" VARCHAR(100),
    "rekening_type" VARCHAR(50),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "total_nominal" BIGINT,
    "metode_pembayaran" VARCHAR(50),
    "status_pembayaran" VARCHAR(50),
    "nominal_dibayar" BIGINT,
    "sisa_piutang_hutang" BIGINT,
    "jatuh_tempo" BIGINT,
    "nama_pihak" VARCHAR(255),
    "kontak_pihak" VARCHAR(100),
    "pihak_type" VARCHAR(50),
    "lokasi_gudang" VARCHAR(255),
    "jenis_aset" VARCHAR(50),
    "kategori_beban" VARCHAR(50),
    "kategori_arus_kas" VARCHAR(50),
    "is_prive" BOOLEAN NOT NULL DEFAULT false,
    "is_modal" BOOLEAN NOT NULL DEFAULT false,
    "pajak_amount" BIGINT,
    "akun_perkiraan_id" VARCHAR(100),
    "penyusutan_per_tahun" BIGINT,
    "umur_manfaat" INTEGER,
    "periode_pelaporan" VARCHAR(7),
    "keterangan" TEXT,

    CONSTRAINT "transaksi_harian_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "item_transaksi" (
    "id" TEXT NOT NULL,
    "transaksi_id" TEXT NOT NULL,
    "nama_produk" TEXT NOT NULL,
    "kategori_path" VARCHAR(500),
    "level1" VARCHAR(100),
    "level2" VARCHAR(100),
    "level3" VARCHAR(100),
    "level4" VARCHAR(100),
    "jumlah" DOUBLE PRECISION NOT NULL,
    "satuan" VARCHAR(50) NOT NULL,
    "harga_satuan" BIGINT NOT NULL,
    "subtotal" BIGINT NOT NULL,
    "produk_id" VARCHAR(100),
    "keterangan" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "item_transaksi_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "hpp_breakdown" (
    "id" TEXT NOT NULL,
    "transaksi_id" TEXT NOT NULL,
    "biaya_bahan_baku" BIGINT,
    "biaya_tenaga_kerja" BIGINT,
    "biaya_lainnya" BIGINT,
    "total_hpp" BIGINT NOT NULL,
    "detail_json" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "hpp_breakdown_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "inventory_impact" (
    "id" TEXT NOT NULL,
    "transaksi_id" TEXT NOT NULL,
    "is_tracked" BOOLEAN NOT NULL,
    "jenis_movement" VARCHAR(50),
    "lokasi_gudang" VARCHAR(255),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "inventory_impact_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "item_inventory" (
    "id" TEXT NOT NULL,
    "inventory_impact_id" TEXT NOT NULL,
    "produk_id" VARCHAR(100) NOT NULL,
    "jumlah_movement" DOUBLE PRECISION NOT NULL,
    "stok_setelah" DOUBLE PRECISION NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "item_inventory_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "outbox" (
    "id" TEXT NOT NULL,
    "transaksi_id" TEXT NOT NULL,
    "event_type" VARCHAR(100) NOT NULL,
    "payload" JSONB NOT NULL,
    "processed" BOOLEAN NOT NULL DEFAULT false,
    "processed_at" TIMESTAMP(3),
    "retry_count" INTEGER NOT NULL DEFAULT 0,
    "error_message" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "outbox_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "tax_info" (
    "id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "periode" VARCHAR(7) NOT NULL,
    "omzet_bulan_ini" BIGINT NOT NULL DEFAULT 0,
    "omzet_tahun_berjalan" BIGINT NOT NULL DEFAULT 0,
    "exceeds_500juta" BOOLEAN NOT NULL DEFAULT false,
    "exceeds_4_8milyar" BOOLEAN NOT NULL DEFAULT false,
    "pph_final_terutang" BIGINT NOT NULL DEFAULT 0,
    "pph_final_terbayar" BIGINT NOT NULL DEFAULT 0,
    "is_pkp" BOOLEAN NOT NULL DEFAULT false,
    "status_wp" VARCHAR(50),
    "tahun_terdaftar" INTEGER,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "tax_info_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "User_email_key" ON "User"("email");

-- CreateIndex
CREATE UNIQUE INDEX "User_username_key" ON "User"("username");

-- CreateIndex
CREATE UNIQUE INDEX "Account_provider_providerAccountId_key" ON "Account"("provider", "providerAccountId");

-- CreateIndex
CREATE UNIQUE INDEX "Session_sessionToken_key" ON "Session"("sessionToken");

-- CreateIndex
CREATE UNIQUE INDEX "UserSecurity_userId_key" ON "UserSecurity"("userId");

-- CreateIndex
CREATE UNIQUE INDEX "UserProfile_userId_key" ON "UserProfile"("userId");

-- CreateIndex
CREATE UNIQUE INDEX "UserProfile_publicUrlSlug_key" ON "UserProfile"("publicUrlSlug");

-- CreateIndex
CREATE UNIQUE INDEX "VerificationToken_token_key" ON "VerificationToken"("token");

-- CreateIndex
CREATE UNIQUE INDEX "VerificationToken_identifier_token_key" ON "VerificationToken"("identifier", "token");

-- CreateIndex
CREATE UNIQUE INDEX "UserAISettings_botSlug_key" ON "UserAISettings"("botSlug");

-- CreateIndex
CREATE UNIQUE INDEX "Tenant_alias_key" ON "Tenant"("alias");

-- CreateIndex
CREATE INDEX "audit_logs_userId_idx" ON "audit_logs"("userId");

-- CreateIndex
CREATE INDEX "audit_logs_eventType_idx" ON "audit_logs"("eventType");

-- CreateIndex
CREATE INDEX "audit_logs_createdAt_idx" ON "audit_logs"("createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "transaksi_harian_idempotency_key_key" ON "transaksi_harian"("idempotency_key");

-- CreateIndex
CREATE INDEX "transaksi_harian_tenant_id_idx" ON "transaksi_harian"("tenant_id");

-- CreateIndex
CREATE INDEX "transaksi_harian_timestamp_idx" ON "transaksi_harian"("timestamp");

-- CreateIndex
CREATE INDEX "transaksi_harian_jenis_transaksi_idx" ON "transaksi_harian"("jenis_transaksi");

-- CreateIndex
CREATE INDEX "transaksi_harian_status_idx" ON "transaksi_harian"("status");

-- CreateIndex
CREATE INDEX "transaksi_harian_tenant_id_status_idx" ON "transaksi_harian"("tenant_id", "status");

-- CreateIndex
CREATE INDEX "transaksi_harian_tenant_id_timestamp_idx" ON "transaksi_harian"("tenant_id", "timestamp" DESC);

-- CreateIndex
CREATE INDEX "transaksi_harian_created_by_idx" ON "transaksi_harian"("created_by");

-- CreateIndex
CREATE INDEX "transaksi_harian_idempotency_key_idx" ON "transaksi_harian"("idempotency_key");

-- CreateIndex
CREATE INDEX "transaksi_harian_tenant_id_periode_pelaporan_idx" ON "transaksi_harian"("tenant_id", "periode_pelaporan");

-- CreateIndex
CREATE INDEX "transaksi_harian_tenant_id_kategori_arus_kas_idx" ON "transaksi_harian"("tenant_id", "kategori_arus_kas");

-- CreateIndex
CREATE INDEX "transaksi_harian_nama_pihak_idx" ON "transaksi_harian"("nama_pihak");

-- CreateIndex
CREATE INDEX "item_transaksi_transaksi_id_idx" ON "item_transaksi"("transaksi_id");

-- CreateIndex
CREATE INDEX "item_transaksi_level1_idx" ON "item_transaksi"("level1");

-- CreateIndex
CREATE INDEX "item_transaksi_level2_idx" ON "item_transaksi"("level2");

-- CreateIndex
CREATE INDEX "item_transaksi_produk_id_idx" ON "item_transaksi"("produk_id");

-- CreateIndex
CREATE UNIQUE INDEX "hpp_breakdown_transaksi_id_key" ON "hpp_breakdown"("transaksi_id");

-- CreateIndex
CREATE INDEX "hpp_breakdown_transaksi_id_idx" ON "hpp_breakdown"("transaksi_id");

-- CreateIndex
CREATE UNIQUE INDEX "inventory_impact_transaksi_id_key" ON "inventory_impact"("transaksi_id");

-- CreateIndex
CREATE INDEX "inventory_impact_transaksi_id_idx" ON "inventory_impact"("transaksi_id");

-- CreateIndex
CREATE INDEX "inventory_impact_jenis_movement_idx" ON "inventory_impact"("jenis_movement");

-- CreateIndex
CREATE INDEX "item_inventory_inventory_impact_id_idx" ON "item_inventory"("inventory_impact_id");

-- CreateIndex
CREATE INDEX "item_inventory_produk_id_idx" ON "item_inventory"("produk_id");

-- CreateIndex
CREATE INDEX "outbox_processed_created_at_idx" ON "outbox"("processed", "created_at");

-- CreateIndex
CREATE INDEX "outbox_transaksi_id_idx" ON "outbox"("transaksi_id");

-- CreateIndex
CREATE INDEX "outbox_event_type_idx" ON "outbox"("event_type");

-- CreateIndex
CREATE INDEX "tax_info_tenant_id_idx" ON "tax_info"("tenant_id");

-- CreateIndex
CREATE INDEX "tax_info_periode_idx" ON "tax_info"("periode");

-- CreateIndex
CREATE INDEX "tax_info_tenant_id_exceeds_500juta_exceeds_4_8milyar_idx" ON "tax_info"("tenant_id", "exceeds_500juta", "exceeds_4_8milyar");

-- CreateIndex
CREATE UNIQUE INDEX "tax_info_tenant_id_periode_key" ON "tax_info"("tenant_id", "periode");

-- AddForeignKey
ALTER TABLE "User" ADD CONSTRAINT "User_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Account" ADD CONSTRAINT "Account_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Session" ADD CONSTRAINT "Session_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserSecurity" ADD CONSTRAINT "UserSecurity_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserProfile" ADD CONSTRAINT "UserProfile_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserBusiness" ADD CONSTRAINT "UserBusiness_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserLocations" ADD CONSTRAINT "UserLocations_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserFinance" ADD CONSTRAINT "UserFinance_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserSubscriptions" ADD CONSTRAINT "UserSubscriptions_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserSubscriptions" ADD CONSTRAINT "UserSubscriptions_planId_fkey" FOREIGN KEY ("planId") REFERENCES "Plans"("planId") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserAISettings" ADD CONSTRAINT "UserAISettings_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "UserMedia" ADD CONSTRAINT "UserMedia_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transaksi_harian" ADD CONSTRAINT "transaksi_harian_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transaksi_harian" ADD CONSTRAINT "transaksi_harian_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transaksi_harian" ADD CONSTRAINT "transaksi_harian_approved_by_fkey" FOREIGN KEY ("approved_by") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "item_transaksi" ADD CONSTRAINT "item_transaksi_transaksi_id_fkey" FOREIGN KEY ("transaksi_id") REFERENCES "transaksi_harian"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "hpp_breakdown" ADD CONSTRAINT "hpp_breakdown_transaksi_id_fkey" FOREIGN KEY ("transaksi_id") REFERENCES "transaksi_harian"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "inventory_impact" ADD CONSTRAINT "inventory_impact_transaksi_id_fkey" FOREIGN KEY ("transaksi_id") REFERENCES "transaksi_harian"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "item_inventory" ADD CONSTRAINT "item_inventory_inventory_impact_id_fkey" FOREIGN KEY ("inventory_impact_id") REFERENCES "inventory_impact"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "outbox" ADD CONSTRAINT "outbox_transaksi_id_fkey" FOREIGN KEY ("transaksi_id") REFERENCES "transaksi_harian"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "tax_info" ADD CONSTRAINT "tax_info_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;


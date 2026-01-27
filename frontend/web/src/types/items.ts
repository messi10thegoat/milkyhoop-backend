/**
 * Types for Items (Master Data) module.
 *
 * Items are the master data for goods and services used in transactions.
 * - Goods can have track_inventory=true to appear in Inventory views
 * - Services never track inventory
 */

// =============================================================================
// ENUMS & LITERALS
// =============================================================================

export type ItemType = 'goods' | 'service';

// =============================================================================
// UNIT CONVERSION
// =============================================================================

export interface UnitConversion {
  id?: string;
  baseUnit: string;
  conversionUnit: string;
  conversionFactor: number;
  purchasePrice?: number;
  salesPrice?: number;
  isActive?: boolean;
}

export interface UnitConversionInput {
  conversionUnit: string;
  conversionFactor: number;
  purchasePrice?: number;
  salesPrice?: number;
}

// =============================================================================
// ITEM DATA STRUCTURES
// =============================================================================

export interface Item {
  id: string;
  name: string;
  itemType: ItemType;
  trackInventory: boolean;
  baseUnit: string;
  barcode?: string;
  kategori?: string;
  deskripsi?: string;
  isReturnable: boolean;
  salesAccount: string;
  purchaseAccount: string;
  salesTax?: string;
  purchaseTax?: string;
  salesPrice?: number;
  purchasePrice?: number;
  currentStock?: number;
  stockValue?: number;
  conversions: UnitConversion[];
  createdAt: string;
  updatedAt: string;
}

export interface ItemListItem {
  id: string;
  name: string;
  itemType: ItemType;
  trackInventory: boolean;
  baseUnit: string;
  barcode?: string;
  kategori?: string;
  isReturnable: boolean;
  salesPrice?: number;
  purchasePrice?: number;
  currentStock?: number;
  stockValue?: number;
  createdAt: string;
  updatedAt: string;
}

// =============================================================================
// FORM DATA
// =============================================================================

export interface CreateItemData {
  name: string;
  itemType: ItemType;
  trackInventory: boolean;
  baseUnit: string;
  barcode?: string;
  kategori?: string;
  deskripsi?: string;
  isReturnable: boolean;
  forSales: boolean;      // Zoho Books requires: at least one must be true
  forPurchases: boolean;  // Zoho Books requires: at least one must be true
  salesAccount: string;
  purchaseAccount: string;
  salesTax?: string;
  purchaseTax?: string;
  salesPrice?: number;
  purchasePrice?: number;
  salesAccountId?: string;
  purchaseAccountId?: string;
  preferredVendorId?: string;
  reorderLevel?: number;
  imageUrl?: string;
  conversions: UnitConversionInput[];
}

export const INITIAL_ITEM_DATA: CreateItemData = {
  name: '',
  itemType: 'goods',
  trackInventory: true,
  baseUnit: '',
  barcode: '',
  kategori: '',
  deskripsi: '',
  isReturnable: true,
  forSales: true,       // Default: item is for sales
  forPurchases: true,   // Default: item is for purchases
  salesAccount: 'Sales',
  purchaseAccount: 'Cost of Goods Sold',
  salesTax: '',
  purchaseTax: '',
  salesPrice: undefined,
  purchasePrice: undefined,
  salesAccountId: undefined,
  purchaseAccountId: undefined,
  preferredVendorId: undefined,
  reorderLevel: undefined,
  imageUrl: undefined,
  conversions: [],
};

// =============================================================================
// API RESPONSE TYPES
// =============================================================================

export interface ItemListResponse {
  success: boolean;
  items: ItemListItem[];
  total: number;
  hasMore: boolean;
}

export interface ItemDetailResponse {
  success: boolean;
  data: Item;
}

export interface CreateItemResponse {
  success: boolean;
  message: string;
  data?: {
    id: string;
    name: string;
    itemType: ItemType;
    trackInventory: boolean;
  };
}

export interface UpdateItemResponse {
  success: boolean;
  message: string;
  data?: { id: string };
}

export interface DeleteItemResponse {
  success: boolean;
  message: string;
}

// =============================================================================
// UNITS
// =============================================================================

export interface UnitListResponse {
  success: boolean;
  defaultUnits: string[];
  customUnits: string[];
}

export const DEFAULT_UNITS = [
  'Pcs', 'Box', 'Karton', 'Lusin', 'Pack', 'Strip', 'Tablet',
  'Kg', 'Gram', 'Liter', 'Ml', 'Dus', 'Unit', 'Set'
];

// =============================================================================
// ACCOUNTS
// =============================================================================

export interface AccountOption {
  value: string;
  label: string;
  type: 'income' | 'expense' | 'cogs';
}

// Akun Pendapatan (Sales/Income Accounts)
export const SALES_ACCOUNTS: AccountOption[] = [
  { value: 'Penjualan', label: 'Penjualan', type: 'income' },
  { value: 'Pendapatan Jasa', label: 'Pendapatan Jasa', type: 'income' },
  { value: 'Pendapatan Umum', label: 'Pendapatan Umum', type: 'income' },
  { value: 'Pendapatan Bunga', label: 'Pendapatan Bunga', type: 'income' },
  { value: 'Pendapatan Denda', label: 'Pendapatan Denda', type: 'income' },
  { value: 'Pendapatan Lain-lain', label: 'Pendapatan Lain-lain', type: 'income' },
  { value: 'Diskon Penjualan', label: 'Diskon Penjualan', type: 'income' },
  { value: 'Ongkos Kirim', label: 'Ongkos Kirim', type: 'income' },
];

// Akun Beban (Purchase/Expense Accounts)
export const PURCHASE_ACCOUNTS: AccountOption[] = [
  { value: 'Harga Pokok Penjualan', label: 'Harga Pokok Penjualan', type: 'cogs' },
  { value: 'Pembelian', label: 'Pembelian', type: 'cogs' },
  { value: 'Biaya Iklan & Pemasaran', label: 'Biaya Iklan & Pemasaran', type: 'expense' },
  { value: 'Biaya Kendaraan', label: 'Biaya Kendaraan', type: 'expense' },
  { value: 'Piutang Tak Tertagih', label: 'Piutang Tak Tertagih', type: 'expense' },
  { value: 'Biaya Bank', label: 'Biaya Bank', type: 'expense' },
  { value: 'Biaya Konsultan', label: 'Biaya Konsultan', type: 'expense' },
  { value: 'Biaya Kartu Kredit', label: 'Biaya Kartu Kredit', type: 'expense' },
  { value: 'Biaya Penyusutan', label: 'Biaya Penyusutan', type: 'expense' },
  { value: 'Biaya IT & Internet', label: 'Biaya IT & Internet', type: 'expense' },
  { value: 'Biaya Kebersihan', label: 'Biaya Kebersihan', type: 'expense' },
  { value: 'Biaya Penginapan', label: 'Biaya Penginapan', type: 'expense' },
  { value: 'Biaya Makan & Hiburan', label: 'Biaya Makan & Hiburan', type: 'expense' },
  { value: 'Perlengkapan Kantor', label: 'Perlengkapan Kantor', type: 'expense' },
  { value: 'Biaya Lain-lain', label: 'Biaya Lain-lain', type: 'expense' },
  { value: 'Biaya Pengiriman', label: 'Biaya Pengiriman', type: 'expense' },
  { value: 'Biaya Cetak & ATK', label: 'Biaya Cetak & ATK', type: 'expense' },
  { value: 'Diskon Pembelian', label: 'Diskon Pembelian', type: 'expense' },
  { value: 'Biaya Sewa', label: 'Biaya Sewa', type: 'expense' },
  { value: 'Biaya Perbaikan & Pemeliharaan', label: 'Biaya Perbaikan & Pemeliharaan', type: 'expense' },
  { value: 'Gaji & Upah Karyawan', label: 'Gaji & Upah Karyawan', type: 'expense' },
  { value: 'Biaya Telepon', label: 'Biaya Telepon', type: 'expense' },
  { value: 'Biaya Perjalanan', label: 'Biaya Perjalanan', type: 'expense' },
  { value: 'Tidak Dikategorikan', label: 'Tidak Dikategorikan', type: 'expense' },
];

// =============================================================================
// TAX OPTIONS
// =============================================================================

export interface TaxOption {
  value: string;
  label: string;
  rate: number;
}

export const TAX_OPTIONS: TaxOption[] = [
  { value: '', label: 'Tidak Ada', rate: 0 },
  { value: 'PPN_11', label: 'PPN 11%', rate: 11 },
  { value: 'PPN_12', label: 'PPN 12%', rate: 12 },
];

export const SERVICE_TAX_OPTIONS: TaxOption[] = [
  { value: '', label: 'Tidak Ada', rate: 0 },
  { value: 'PPN_11', label: 'PPN 11%', rate: 11 },
  { value: 'PPN_12', label: 'PPN 12%', rate: 12 },
  { value: 'PPH_23_2', label: 'PPh 23 - 2% (Jasa)', rate: 2 },
  { value: 'PPH_23_15', label: 'PPh 23 - 15% (Dividen/Royalti)', rate: 15 },
];

// =============================================================================
// COMPONENT PROPS
// =============================================================================

export interface AddItemFormProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (item: CreateItemResponse['data']) => void;
  /** Item to edit - if provided, form opens in edit mode */
  editItem?: ItemListItem | null;
}

export interface ItemsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  isMobile?: boolean;
  isEmbedded?: boolean;
}

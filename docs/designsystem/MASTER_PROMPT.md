# Milkyhoop Frontend Module Generator - Master Prompt

Gunakan prompt ini untuk membuat modul baru yang konsisten dengan design system MilkyHoop.

---

## System Prompt (Copy ini ke awal chat)

```
Kamu adalah Frontend Developer expert untuk aplikasi MilkyHoop, sebuah software akuntansi Indonesia.

## Tech Stack
- React 18 dengan TypeScript
- Inline styles (bukan className/CSS modules)
- Framer Motion untuk animasi
- Plus Jakarta Sans font

## Design Philosophy
- Mobile-first dengan maxWidth 430px
- Soft earthy tones (olive accent #8B9A5B)
- Pill-shaped components (borderRadius: 100px)
- Smooth transitions (0.15s - 0.3s ease)
- Indonesian language untuk semua UI text

## Color Palette
Background: #FFFFFF, #F7F6F3, #EFEEE9, #E8E7E2
Text: #1A1A1A, #6B6B6B, #9A9A9A
Accent: #8B9A5B (olive), #E8EBD9 (olive light), #C5CBA8 (olive soft)
Border: #E8E6E1, #E5E5E5

## Component Patterns
1. Panel: Full-screen slide-in dari kanan
2. FieldPill: Expandable pill field
3. ToggleField: Toggle switch dengan label pill
4. BottomSheet: Modal dari bawah
5. AccordionCard: List item dengan expand/collapse
6. SummaryBar: Fixed bottom dengan expandable breakdown

## Code Style
- Inline styles dengan objek JavaScript
- SVG icons inline (bukan icon library)
- Hooks terpisah (useXxxForm.ts)
- Constants terpisah (constants.ts)
- Types di folder types/

Ikuti pattern dari modul yang sudah ada: SalesInvoice, PurchaseInvoice, Vendor, Expenses, Items.
```

---

## Prompt Template untuk Modul Baru

### Step 1: Describe the Module

```
Buatkan modul [NAMA_MODUL] untuk MilkyHoop dengan spesifikasi berikut:

## Informasi Modul
- Nama: [Nama Modul]
- Judul (ID): [Judul Bahasa Indonesia]
- Kategori: [master-data | transaction | report]

## Fitur List View
- Summary card: [ya/tidak]
- Search dengan filter: [ya/tidak]
- Sort options: [sebutkan field]

## Field pada List Item
- Kolom kiri: [sebutkan field]
- Kolom kanan: [sebutkan field]
- Status indicator: [sebutkan status]

## Field pada Form Create/Edit
Wajib:
- [field 1]: [type] - [deskripsi]
- [field 2]: [type] - [deskripsi]

Opsional:
- [field 1]: [type] - [deskripsi]
- [field 2]: [type] - [deskripsi]

## Actions
- [action 1]: [deskripsi]
- [action 2]: [deskripsi]
```

### Step 2: Request Specific Components

```
Berdasarkan spec di atas, buatkan:

1. [ModuleName]Panel.tsx - Main panel dengan list view
2. [ModuleName]Card.tsx - List item card
3. Create[ModuleName]/index.tsx - Form create/edit
4. hooks/use[ModuleName]Form.ts - Form logic hook
5. types/[modulename].ts - TypeScript types
6. constants.ts - Constants dan mock data
```

---

## Quick Prompts untuk Komponen Spesifik

### Prompt: Panel dengan List View

```
Buatkan [ModuleName]Panel.tsx untuk MilkyHoop dengan:
- Header: back button, title "[Judul]", plus button
- Search bar dengan tombol Filter
- Stats card dengan [segment1, segment2, ...]
- List dengan AccordionCard pattern
- Empty state ketika list kosong

Data yang ditampilkan per item:
- Primary: [field]
- Secondary: [field]
- Amount: [field]
- Status: [field]

Gunakan pattern dari VendorPanel atau ExpensesPanel.
```

### Prompt: Create Form

```
Buatkan Create[ModuleName] form untuk MilkyHoop dengan:

Wajib diisi:
1. [Field] - type [FieldPill|selection|date|...]
2. [Field] - type [...]
3. [Field] - type [...]

Opsional:
1. [Field] - type [...]
2. [Field] - type [...]

Dengan SummaryBar di bottom yang menampilkan:
- [Subtotal row]
- [Discount row]
- [Tax row]
- [Total row]

Gunakan pattern dari CreateInvoice (Sales atau Purchase).
```

### Prompt: Bottom Sheet

```
Buatkan [SheetName]Sheet.tsx untuk MilkyHoop:
- Title: "[Judul]"
- Content: [deskripsi content]
- Actions: [sebutkan button/actions]

Gunakan BottomSheet component sebagai base wrapper.
```

### Prompt: Form Hook

```
Buatkan use[ModuleName]Form.ts hook dengan:

State:
- formData dengan fields: [sebutkan fields]
- isSubmitting, error, isValid

Functions:
- updateField(key, value)
- validate()
- submit()
- [fungsi khusus lainnya]

Kalkulasi (jika ada):
- [subtotal calculation]
- [tax calculation]
- [total calculation]
```

---

## Contoh Prompt Lengkap

### Contoh: Modul Fixed Asset

```
Buatkan modul Fixed Asset (Aset Tetap) untuk MilkyHoop.

## Informasi
- Nama: FixedAsset
- Judul: Aset Tetap
- Kategori: master-data

## List View
- Summary card: Ya, tampilkan total nilai aset dan nilai penyusutan
- Search dengan filter
- Sort by: nama, nilai, tanggal perolehan

## Field List Item
- Kiri: nama aset, kode aset
- Kanan: nilai perolehan, status (aktif/dijual/dihapus)
- Expanded: lokasi, tanggal perolehan, nilai buku, akumulasi penyusutan

## Field Form
Wajib:
- nama: text - Nama aset
- kode: text - Kode aset unik
- kategori: selection - Kategori aset
- tanggal_perolehan: date - Tanggal perolehan
- nilai_perolehan: currency - Nilai/harga perolehan
- masa_manfaat: number - Masa manfaat (bulan)
- metode_penyusutan: selection - Garis lurus/Saldo menurun

Opsional:
- lokasi: text - Lokasi aset
- vendor: selection - Vendor pembelian
- catatan: textarea - Catatan tambahan
- foto: file - Foto aset

## Actions
- Lihat detail
- Jual aset
- Hapus aset

Buatkan komponen lengkap:
1. FixedAssetPanel.tsx
2. FixedAssetCard.tsx
3. CreateFixedAsset/index.tsx
4. hooks/useFixedAssetForm.ts
5. types/fixedasset.ts
```

---

## Code Snippets Reference

### Panel Skeleton

```tsx
import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

const COLORS = {
  bgPrimary: '#FFFFFF',
  bgCard: '#F7F6F3',
  bgCardInner: '#EFEEE9',
  textPrimary: '#1A1A1A',
  textSecondary: '#6B6B6B',
  textMuted: '#9A9A9A',
  borderColor: '#E8E6E1',
  olive: '#8B9A5B',
  oliveLighter: '#E8EBD9',
};

const SPRING = { duration: 0.3, ease: [0.32, 0.72, 0, 1] };

interface [ModuleName]PanelProps {
  isOpen: boolean;
  onClose: () => void;
}

const [ModuleName]Panel: React.FC<[ModuleName]PanelProps> = ({ isOpen, onClose }) => {
  const [items, setItems] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
      // Fetch data
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <motion.div
      initial={{ x: '100%' }}
      animate={{ x: 0 }}
      exit={{ x: '100%' }}
      transition={SPRING}
      style={{
        position: 'fixed',
        inset: 0,
        maxWidth: '430px',
        margin: '0 auto',
        background: COLORS.bgPrimary,
        zIndex: 50,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header */}
      <header style={{
        position: 'sticky',
        top: 0,
        background: COLORS.bgPrimary,
        zIndex: 50,
        borderBottom: `1px solid ${COLORS.borderColor}`,
      }}>
        {/* ... */}
      </header>

      {/* Content */}
      <main style={{
        flex: 1,
        overflowY: 'auto',
        padding: '16px 20px 100px',
      }}>
        {/* Stats Card */}
        {/* List Items */}
        {/* Empty State */}
      </main>
    </motion.div>
  );
};

export default [ModuleName]Panel;
```

### Form Skeleton

```tsx
import React, { useState } from 'react';
import { motion } from 'framer-motion';
import FieldPill from './FieldPill';
import ToggleField from './ToggleField';
import BottomSheet from './sheets/BottomSheet';
import useForm from './hooks/use[ModuleName]Form';

interface Create[ModuleName]Props {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

const Create[ModuleName]: React.FC<Create[ModuleName]Props> = ({
  isOpen,
  onClose,
  onSuccess
}) => {
  const form = useForm();
  const [activeSheet, setActiveSheet] = useState<string | null>(null);
  const [expandedField, setExpandedField] = useState<string | null>(null);

  const handleSave = async () => {
    const success = await form.submit();
    if (success) {
      onSuccess?.();
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <motion.div
      initial={{ x: '100%' }}
      animate={{ x: 0 }}
      exit={{ x: '100%' }}
      transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        background: '#FFFFFF',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header */}
      {/* Form Content */}
      {/* Summary Bar */}
      {/* Sheets */}
    </motion.div>
  );
};

export default Create[ModuleName];
```

### Hook Skeleton

```tsx
import { useState, useCallback, useMemo } from 'react';

interface FormData {
  // Define fields
}

interface Breakdown {
  subtotal: number;
  discountAmount: number;
  taxAmount: number;
  grandTotal: number;
}

export default function use[ModuleName]Form() {
  const [formData, setFormData] = useState<FormData>({
    // Initial values
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateField = useCallback(<K extends keyof FormData>(
    key: K,
    value: FormData[K]
  ) => {
    setFormData(prev => ({ ...prev, [key]: value }));
  }, []);

  const breakdown = useMemo<Breakdown>(() => {
    // Calculate breakdown
    return {
      subtotal: 0,
      discountAmount: 0,
      taxAmount: 0,
      grandTotal: 0,
    };
  }, [formData]);

  const isValid = useMemo(() => {
    // Validation logic
    return true;
  }, [formData]);

  const submit = useCallback(async (): Promise<boolean> => {
    if (!isValid) return false;
    setIsSubmitting(true);
    setError(null);

    try {
      // API call
      return true;
    } catch (err) {
      setError('Gagal menyimpan data');
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [formData, isValid]);

  return {
    formData,
    updateField,
    breakdown,
    isValid,
    isSubmitting,
    error,
    submit,
  };
}
```

---

## Checklist untuk Review

Setelah generate kode, pastikan:

- [ ] Warna sesuai dengan design tokens
- [ ] Font Plus Jakarta Sans
- [ ] BorderRadius 100px untuk pill-shaped elements
- [ ] Animation menggunakan Framer Motion dengan ease [0.32, 0.72, 0, 1]
- [ ] Max-width 430px untuk panel
- [ ] Safe area padding (env(safe-area-inset-*))
- [ ] Indonesian language untuk semua label
- [ ] Icons menggunakan SVG inline
- [ ] Loading state dan error state handled
- [ ] Empty state dengan ilustrasi dan pesan
- [ ] Form validation
- [ ] TypeScript types lengkap

---

## Tips

1. **Selalu refer ke modul existing** - SalesInvoice, PurchaseInvoice, Vendor, Expenses, Items
2. **Copy pattern, bukan reinvent** - Gunakan komponen yang sudah ada
3. **Konsisten dengan naming** - camelCase untuk variabel, PascalCase untuk komponen
4. **Indonesian untuk UI** - English untuk code comments dan variable names
5. **Mobile-first thinking** - Test di viewport 375px

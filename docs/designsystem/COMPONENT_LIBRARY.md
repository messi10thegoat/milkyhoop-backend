# Milkyhoop Component Library

Dokumentasi komponen UI yang digunakan di frontend MilkyHoop, extracted dari production code.

## Overview

- **Framework**: React TypeScript
- **Build**: Create React App (CRA)
- **Styling**: Inline styles + TailwindCSS utilities
- **Animation**: Framer Motion
- **Font**: Plus Jakarta Sans

---

## 1. Design Tokens

### Colors

```typescript
const COLORS = {
  // Backgrounds
  bgPrimary: '#FFFFFF',
  bgCard: '#F7F6F3',
  bgCardInner: '#EFEEE9',
  bgCardDarker: '#E8E7E2',
  bgHover: '#E2E1DC',
  bgOverlay: 'rgba(0,0,0,0.4)',

  // Text
  textPrimary: '#1A1A1A',
  textSecondary: '#6B6B6B',
  textMuted: '#9A9A9A',
  textInverse: '#FFFFFF',

  // Accent - Olive (Primary Brand)
  olive: '#8B9A5B',
  oliveLight: '#F4F6EE',
  oliveSoft: '#C5CBA8',
  oliveLighter: '#E8EBD9',

  // Semantic - Status Colors
  success: '#5B8C51',
  warning: '#C9A227',
  danger: '#C45C4B',
  info: '#4A8DB7',

  // Borders
  borderColor: '#E8E6E1',
  borderDashed: '#C5C5C5',
  dividerColor: '#E5E5E5',
};
```

### Radius

```typescript
const RADIUS = {
  sm: '6px',
  md: '12px',
  lg: '14px',
  xl: '16px',
  '2xl': '20px',
  '3xl': '24px',
  full: '100px',  // Pill-shaped
};
```

### Typography

```typescript
const TYPOGRAPHY = {
  fontFamily: "'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif",
  sizes: {
    xs: '11px',
    sm: '12px',
    base: '13px',
    md: '14px',
    lg: '15px',
    xl: '16px',
    '2xl': '18px',
    '3xl': '20px',
    hero: '36px',  // Summary amount
  },
  weights: {
    normal: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
    extrabold: 800,
  },
};
```

### Shadows

```typescript
const SHADOWS = {
  sm: '0 1px 2px rgba(0,0,0,0.05)',
  md: '0 1px 3px rgba(0,0,0,0.1)',
  lg: '0 4px 6px rgba(0,0,0,0.1)',
  sheet: '0 -4px 20px rgba(0,0,0,0.08)',
};
```

### Transitions & Animation

```typescript
const TRANSITIONS = {
  fast: '0.15s ease',
  normal: '0.2s ease',
  smooth: '0.3s cubic-bezier(0.4, 0, 0.2, 1)',
};

// Framer Motion Spring
const FRAMER_SPRING = {
  duration: 0.3,
  ease: [0.32, 0.72, 0, 1],
};
```

### Z-Index Layers

```typescript
const Z_INDEX = {
  base: 0,
  summary: 40,
  header: 50,
  panel: 60,
  overlay: 150,
  sheet: 200,
};
```

---

## 2. Layout Components

### Panel (Full-Screen Module)

Panel adalah komponen full-screen untuk modul utama seperti Sales Invoice, Purchase Invoice, Vendor, dll.

```tsx
<motion.div
  initial={{ x: '100%' }}
  animate={{ x: 0 }}
  exit={{ x: '100%' }}
  transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
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
  <header>...</header>
  <main style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 100px' }}>...</main>
  <BottomNavigation />
</motion.div>
```

**Props Pattern:**
```typescript
interface PanelProps {
  isOpen: boolean;
  onClose: () => void;
  isMobile?: boolean;
  isEmbedded?: boolean;
  onCreateNew?: () => void;
}
```

### Panel Header

Header sticky dengan back button, title, dan action button.

```tsx
<header style={{
  position: 'sticky',
  top: 0,
  background: COLORS.bgPrimary,
  zIndex: 50,
  borderBottom: `1px solid ${COLORS.borderColor}`,
  paddingTop: 'env(safe-area-inset-top)',
}}>
  <div style={{
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 20px',
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <button onClick={onClose}><IconBack /></button>
      <h1 style={{ fontSize: '20px', fontWeight: 700, color: COLORS.textPrimary }}>
        {title}
      </h1>
    </div>
    <button onClick={onCreateNew}><IconPlus /></button>
  </div>
</header>
```

### Search Bar Trigger

Clickable search bar yang membuka full search page.

```tsx
<div
  onClick={() => setShowSearch(true)}
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '12px 16px',
    background: COLORS.bgCard,
    borderRadius: '16px',
    cursor: 'pointer',
  }}
>
  <IconSearch />
  <span style={{ flex: 1, fontSize: '14px', color: COLORS.textMuted }}>
    Cari faktur atau pelanggan...
  </span>
  <button style={{
    padding: '4px 10px',
    background: COLORS.bgCardInner,
    borderRadius: '100px',
    fontSize: '13px',
    fontWeight: 600,
    color: COLORS.textSecondary,
  }}>
    Filter
  </button>
</div>
```

---

## 3. Stats & Summary Components

### Stats Card (Summary Overview)

Card untuk menampilkan summary dengan total, progress bar, dan legend.

```tsx
<div style={{
  padding: '20px',
  background: COLORS.bgCard,
  borderRadius: '24px',
  marginBottom: '20px',
}}>
  {/* Header */}
  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
    <span style={{
      fontSize: '13px',
      fontWeight: 600,
      color: COLORS.textMuted,
      textTransform: 'uppercase',
      letterSpacing: '0.5px',
    }}>
      Total Piutang
    </span>
    <span style={{ fontSize: '13px', color: COLORS.textMuted }}>
      {currentPeriod}
    </span>
  </div>

  {/* Hero Amount */}
  <div style={{
    fontSize: '36px',
    fontWeight: 800,
    color: COLORS.textPrimary,
    letterSpacing: '-1.5px',
    lineHeight: 1.1,
    marginBottom: '16px',
  }}>
    Rp {formatRupiah(total)}
  </div>

  {/* Progress Bar */}
  <div style={{
    display: 'flex',
    height: '8px',
    borderRadius: '100px',
    overflow: 'hidden',
    gap: '2px',
    marginBottom: '16px',
  }}>
    {bars.map(bar => (
      <div key={bar.id} style={{
        width: `${bar.pct}%`,
        background: bar.color,
        borderRadius: '100px',
        opacity: 0.8,
      }} />
    ))}
  </div>

  {/* Legend Items */}
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
    {legends.map(l => <LegendItem key={l.id} {...l} />)}
  </div>
</div>
```

### Legend Item

Clickable legend untuk filtering.

```tsx
<button
  onClick={onClick}
  style={{
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '8px 12px',
    background: active ? COLORS.bgCardDarker : COLORS.bgCardInner,
    border: 'none',
    borderRadius: '100px',
    cursor: 'pointer',
    transition: 'background 0.15s ease',
  }}
>
  <span style={{
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: color,
    opacity: active ? 1 : 0.6,
  }} />
  <span style={{ fontSize: '13px', color: COLORS.textMuted }}>{label}</span>
  <span style={{
    fontSize: '13px',
    fontWeight: 600,
    color: active ? COLORS.textPrimary : COLORS.textSecondary,
  }}>
    Rp {value}
  </span>
</button>
```

### Summary Bar (Fixed Bottom)

Fixed bottom bar dengan expandable breakdown.

```tsx
<div style={{
  position: 'fixed',
  bottom: 0,
  left: 0,
  right: 0,
  maxWidth: '430px',
  margin: '0 auto',
  background: COLORS.bgPrimary,
  borderTop: `1px solid ${COLORS.dividerColor}`,
  boxShadow: '0 -4px 20px rgba(0,0,0,0.08)',
  zIndex: 40,
}}>
  {/* Toggle Header */}
  <div
    onClick={() => setIsExpanded(!isExpanded)}
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '14px 20px',
      cursor: 'pointer',
      background: COLORS.bgCard,
    }}
  >
    <span style={{ fontSize: '12px', fontWeight: 600, textTransform: 'uppercase' }}>
      Total
    </span>
    <span style={{ fontSize: '18px', fontWeight: 800, color: COLORS.textPrimary }}>
      Rp {formatRupiah(grandTotal)}
    </span>
  </div>

  {/* Expandable Details */}
  <AnimatePresence>
    {isExpanded && (
      <motion.div
        initial={{ height: 0, opacity: 0 }}
        animate={{ height: 'auto', opacity: 1 }}
        exit={{ height: 0, opacity: 0 }}
      >
        {/* Breakdown rows */}
      </motion.div>
    )}
  </AnimatePresence>
</div>
```

---

## 4. Form Components

### FieldPill (Expandable Field)

Field dengan pattern: Collapsed → Expanded → Filled.

```tsx
interface FieldPillProps {
  icon: React.ReactNode;
  label: string;
  value?: string | null;
  placeholder?: string;
  isExpanded?: boolean;
  hasValue?: boolean;
  onClick?: () => void;
  onClear?: () => void;
  onActionClick?: () => void;
}

// Collapsed State
<button style={{
  display: 'inline-flex',
  alignItems: 'center',
  gap: '8px',
  padding: '12px 16px',
  borderRadius: '100px',
  background: hasValue ? COLORS.bgCardDarker : COLORS.bgCard,
  border: 'none',
}}>
  {icon}
  <span style={{
    fontSize: '13px',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.3px',
    color: COLORS.textPrimary,
  }}>{label}</span>
  <ChevronDownIcon />
</button>

// Expanded State
<div style={{
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '8px 8px 8px 16px',
  border: hasValue ? '2px solid #E8E6E1' : '2px dashed #C5C5C5',
  borderRadius: '100px',
  background: '#FFFFFF',
}}>
  <span style={{
    color: hasValue ? COLORS.textPrimary : COLORS.textMuted,
    fontWeight: hasValue ? 600 : 500,
    fontSize: hasValue ? '15px' : '14px',
    flex: 1,
  }}>
    {value || placeholder}
  </span>
  <button>{hasValue ? <CloseIcon /> : <PlusIcon />}</button>
</div>
```

### ToggleField

Toggle switch dengan label.

```tsx
interface ToggleFieldProps {
  icon: React.ReactNode;
  label: string;
  isActive: boolean;
  onToggle: () => void;
}

<div style={{
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
}}>
  {/* Label */}
  <div style={{
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '12px 16px',
    background: isActive ? COLORS.oliveLighter : COLORS.bgCard,
    borderRadius: '100px',
  }}>
    {icon}
    <span style={{
      fontSize: '13px',
      fontWeight: 700,
      textTransform: 'uppercase',
    }}>{label}</span>
  </div>

  {/* Toggle Switch */}
  <button style={{
    width: '48px',
    height: '28px',
    background: isActive ? COLORS.oliveSoft : COLORS.bgCardInner,
    borderRadius: '100px',
    position: 'relative',
    border: 'none',
  }}>
    <span style={{
      position: 'absolute',
      top: '3px',
      left: isActive ? '23px' : '3px',
      width: '22px',
      height: '22px',
      background: '#FFFFFF',
      borderRadius: '50%',
      boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
      transition: 'left 0.2s ease',
    }} />
  </button>
</div>
```

### Tax Rate Selector (Segmented Button)

Pilihan tarif pajak dengan segmented button.

```tsx
<div style={{
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  padding: '12px 16px',
  background: COLORS.bgCardInner,
  borderRadius: '100px',
}}>
  <ReceiptIcon />
  <span style={{
    fontSize: '13px',
    fontWeight: 700,
    textTransform: 'uppercase',
    flex: 1,
  }}>PPN</span>
  <div style={{ display: 'flex', gap: '6px' }}>
    {[0, 11, 12].map((rate) => (
      <button
        key={rate}
        onClick={() => setTaxRate(rate)}
        style={{
          padding: '6px 12px',
          borderRadius: '100px',
          border: 'none',
          background: selected ? COLORS.olive : '#FFFFFF',
          color: selected ? '#FFFFFF' : COLORS.textPrimary,
          fontSize: '12px',
          fontWeight: 700,
        }}
      >
        {rate}%
      </button>
    ))}
  </div>
</div>
```

---

## 5. List Components

### Accordion Card (Invoice/Bill Card)

Card dengan expand/collapse untuk detail.

```tsx
<div style={{
  background: COLORS.bgCard,
  borderRadius: '16px',
  marginBottom: '8px',
  overflow: 'hidden',
}}>
  {/* Header - Always visible */}
  <div
    onClick={onToggle}
    style={{
      padding: '14px 16px',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'flex-start',
      gap: '6px',
    }}
  >
    {/* Status dot */}
    <span style={{
      width: '8px',
      height: '8px',
      borderRadius: '50%',
      background: statusColor,
      marginTop: '6px',
    }} />

    {/* Content */}
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: '15px', fontWeight: 600 }}>{name}</div>
      <div style={{ fontSize: '13px', color: COLORS.textMuted }}>{invoiceNo}</div>
    </div>

    {/* Amount */}
    <div style={{ textAlign: 'right' }}>
      <div style={{ fontSize: '15px', fontWeight: 700 }}>Rp {amount}</div>
      <div style={{ fontSize: '13px', color: COLORS.textMuted }}>{status}</div>
    </div>

    {/* Chevron */}
    <div style={{
      transform: expanded ? 'rotate(180deg)' : 'rotate(0)',
      transition: 'transform 0.2s ease',
    }}>
      <IconChevronDown />
    </div>
  </div>

  {/* Expanded Content */}
  {expanded && (
    <div style={{
      padding: '0 16px 16px',
      borderTop: `1px solid ${COLORS.dividerColor}`,
    }}>
      {/* Detail grid, tags, action buttons */}
    </div>
  )}
</div>
```

### Item Card (Line Item)

Card untuk item dalam invoice/bill.

```tsx
<div style={{
  background: '#FFFFFF',
  padding: '14px 16px',
}}>
  {/* Main Info */}
  <div style={{
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: '12px',
  }}>
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: '14px', fontWeight: 600 }}>{productName}</div>
      <div style={{ fontSize: '12px', color: COLORS.textMuted }}>
        {quantity} {unit} × Rp {pricePerUnit}
      </div>
    </div>
    <div style={{ textAlign: 'right' }}>
      <div style={{ fontSize: '14px', fontWeight: 700 }}>Rp {subtotal}</div>
      {discountPercent > 0 && (
        <div style={{ fontSize: '11px', color: COLORS.textMuted }}>
          -{discountPercent}%
        </div>
      )}
    </div>
  </div>

  {/* Actions */}
  <div style={{
    display: 'flex',
    gap: '8px',
    marginTop: '10px',
    paddingTop: '10px',
    borderTop: '1px dashed #C5C5C5',
  }}>
    <button style={{ flex: 1, background: COLORS.bgCard }}>Edit</button>
    <button style={{ flex: 1, background: '#FEF2F2' }}>Hapus</button>
  </div>
</div>
```

### Filter Chip/Tab

Chip untuk filtering dengan count badge.

```tsx
<button style={{
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  padding: '10px 16px',
  background: active ? COLORS.oliveLighter : '#FFFFFF',
  borderRadius: '100px',
  border: `1px solid ${active ? COLORS.oliveSoft : COLORS.borderColor}`,
  fontWeight: active ? 700 : 500,
  color: active ? COLORS.olive : COLORS.textSecondary,
}}>
  <span>{label}</span>
  {count !== undefined && (
    <span style={{
      padding: '2px 8px',
      background: active ? COLORS.oliveSoft : COLORS.bgCard,
      borderRadius: '100px',
      fontSize: '12px',
      fontWeight: 700,
    }}>
      {count}
    </span>
  )}
</button>
```

---

## 6. Sheet Components

### BottomSheet (Base)

Base wrapper untuk semua bottom sheet modal.

```tsx
interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  maxHeight?: string;
}

<AnimatePresence>
  {isOpen && (
    <>
      {/* Overlay */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.4)',
          zIndex: 150,
        }}
      />

      {/* Sheet */}
      <motion.div
        initial={{ y: '100%' }}
        animate={{ y: 0 }}
        exit={{ y: '100%' }}
        transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
        style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          maxWidth: '430px',
          margin: '0 auto',
          background: '#FFFFFF',
          borderRadius: '24px 24px 0 0',
          padding: '12px 20px 32px',
          paddingBottom: 'calc(32px + env(safe-area-inset-bottom))',
          maxHeight: '90vh',
          overflowY: 'auto',
          zIndex: 200,
        }}
      >
        {/* Handle */}
        <div style={{
          width: '40px',
          height: '4px',
          background: COLORS.dividerColor,
          borderRadius: '100px',
          margin: '0 auto 16px',
        }} />

        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '20px',
        }}>
          <h2 style={{ fontSize: '18px', fontWeight: 700 }}>{title}</h2>
          <button style={{
            width: '32px',
            height: '32px',
            background: COLORS.bgCard,
            borderRadius: '50%',
          }}>
            <CloseIcon />
          </button>
        </div>

        {/* Content */}
        {children}
      </motion.div>
    </>
  )}
</AnimatePresence>
```

### Selection Sheet

Sheet untuk memilih dari list (Customer, Vendor, Account).

```tsx
// Inside BottomSheet children
<>
  {/* Search */}
  <div style={{
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '12px 16px',
    background: COLORS.bgCard,
    borderRadius: '12px',
    marginBottom: '16px',
  }}>
    <IconSearch />
    <input
      placeholder="Cari..."
      style={{
        flex: 1,
        border: 'none',
        background: 'none',
        outline: 'none',
      }}
    />
  </div>

  {/* List */}
  <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
    {items.map(item => (
      <div
        key={item.id}
        onClick={() => onSelect(item)}
        style={{
          padding: '14px 16px',
          borderRadius: '12px',
          background: selected ? COLORS.oliveLighter : 'transparent',
          cursor: 'pointer',
          marginBottom: '4px',
        }}
      >
        <div style={{ fontWeight: 600 }}>{item.name}</div>
        <div style={{ fontSize: '12px', color: COLORS.textMuted }}>{item.detail}</div>
      </div>
    ))}
  </div>
</>
```

---

## 7. State Components

### Empty State

State ketika list kosong.

```tsx
<div style={{ textAlign: 'center', padding: '60px 20px' }}>
  <div style={{
    width: '120px',
    height: '120px',
    background: COLORS.bgCard,
    borderRadius: '50%',
    margin: '0 auto 24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  }}>
    <IconDocument style={{ opacity: 0.5 }} />
  </div>
  <div style={{
    fontSize: '18px',
    fontWeight: 700,
    marginBottom: '8px',
    color: COLORS.textPrimary,
  }}>
    Belum ada faktur
  </div>
  <div style={{
    fontSize: '14px',
    color: COLORS.textMuted,
  }}>
    Faktur penjualan yang Anda buat akan muncul di sini
  </div>
</div>
```

### Loading State

Spinner untuk loading.

```tsx
<div style={{ textAlign: 'center', padding: '40px' }}>
  <div style={{
    width: '32px',
    height: '32px',
    border: `3px solid ${COLORS.bgCardDarker}`,
    borderTopColor: COLORS.textMuted,
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
    margin: '0 auto',
  }} />
</div>

// Add keyframe
<style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
```

### Error State

State untuk error dengan retry button.

```tsx
<div style={{ textAlign: 'center', padding: '40px 20px' }}>
  <div style={{ fontSize: '16px', color: COLORS.danger, marginBottom: '16px' }}>
    {errorMessage}
  </div>
  <button
    onClick={onRetry}
    style={{
      padding: '12px 24px',
      background: COLORS.bgCard,
      border: 'none',
      borderRadius: '12px',
      fontSize: '14px',
      fontWeight: 600,
      cursor: 'pointer',
    }}
  >
    Coba lagi
  </button>
</div>
```

### Draft Prompt

Banner untuk draft tersimpan.

```tsx
<div style={{
  padding: '12px 20px',
  background: COLORS.oliveLighter,
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
}}>
  <span style={{ fontSize: '13px', color: COLORS.textPrimary }}>
    Ada draft tersimpan
  </span>
  <div style={{ display: 'flex', gap: '8px' }}>
    <button style={{
      padding: '6px 12px',
      background: 'none',
      border: 'none',
      fontSize: '13px',
      fontWeight: 600,
      color: COLORS.textMuted,
    }}>
      Buang
    </button>
    <button style={{
      padding: '6px 12px',
      background: COLORS.olive,
      borderRadius: '100px',
      fontSize: '13px',
      fontWeight: 600,
      color: '#FFFFFF',
    }}>
      Lanjutkan
    </button>
  </div>
</div>
```

---

## 8. Button Components

### Primary Button

Button dengan warna accent.

```tsx
<button style={{
  width: '100%',
  padding: '16px',
  background: COLORS.olive,
  border: 'none',
  borderRadius: '16px',
  fontSize: '15px',
  fontWeight: 700,
  color: '#FFFFFF',
  cursor: 'pointer',
}}>
  Simpan
</button>
```

### Secondary Button

Button dengan background card.

```tsx
<button style={{
  padding: '12px 24px',
  background: COLORS.bgCard,
  border: 'none',
  borderRadius: '12px',
  fontSize: '14px',
  fontWeight: 600,
  color: COLORS.textSecondary,
  cursor: 'pointer',
}}>
  Batal
</button>
```

### Icon Button

Round button untuk icon.

```tsx
<button style={{
  width: '44px',
  height: '44px',
  background: 'none',
  border: 'none',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
  color: COLORS.textPrimary,
}}>
  <IconBack />
</button>
```

### FAB (Floating Action Button)

Floating button untuk primary action.

```tsx
<motion.button
  initial={{ scale: 0 }}
  animate={{ scale: 1 }}
  whileTap={{ scale: 0.9 }}
  style={{
    position: 'fixed',
    bottom: '24px',
    right: '24px',
    width: '56px',
    height: '56px',
    borderRadius: '50%',
    background: COLORS.olive,
    boxShadow: '0 4px 12px rgba(139, 154, 91, 0.4)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    border: 'none',
    zIndex: 10,
  }}
>
  <PlusIcon />
</motion.button>
```

---

## 9. Icon Components

### Standard Icons (18x18px, 20x20px, 24x24px)

Semua icon menggunakan SVG inline dengan Heroicons style.

```tsx
// 18px - For field icons
<svg style={{ width: '18px', height: '18px', color: '#6B6B6B' }}
  viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
  strokeLinecap="round" strokeLinejoin="round">
  {/* path */}
</svg>

// 20px - For buttons and UI elements
<svg style={{ width: '20px', height: '20px' }}
  viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round">
  {/* path */}
</svg>

// 24px - For header icons
<svg width="24" height="24"
  viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round">
  {/* path */}
</svg>
```

### Common Icons

```tsx
// Back/Chevron Left
<path d="M15 19l-7-7 7-7" />

// Plus
<path d="M12 5v14M5 12h14" />

// Search
<circle cx="11" cy="11" r="8" />
<path d="M21 21l-4.35-4.35" />

// Close (X)
<path d="M6 18L18 6M6 6l12 12" />

// Chevron Down
<path d="M19.5 8.25l-7.5 7.5-7.5-7.5" />

// Edit/Pencil
<path d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />

// Trash
<path d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
```

---

## 10. Utility Functions

### Format Rupiah

```typescript
const formatRupiah = (amount: number): string => {
  return new Intl.NumberFormat('id-ID').format(amount);
};

// With decimal
const formatRupiahDecimal = (amount: number): string => {
  const parts = amount.toFixed(2).split('.');
  const intPart = parseInt(parts[0]).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return intPart + ',' + parts[1];
};
```

### Format Rupiah Short

```typescript
const formatRupiahShort = (amount: number): string => {
  if (amount >= 1e9) return `${(amount / 1e9).toFixed(1)}M`;
  if (amount >= 1e6) return `${(amount / 1e6).toFixed(1)}jt`;
  if (amount >= 1e3) return `${Math.floor(amount / 1e3)}rb`;
  return amount.toString();
};
```

### Format Date

```typescript
const formatDate = (dateStr: string): string => {
  return new Date(dateStr).toLocaleDateString('id-ID', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
};
```

### Get Initials

```typescript
const getInitials = (name: string): string => {
  return name
    .split(' ')
    .map(n => n[0])
    .join('')
    .substring(0, 2)
    .toUpperCase();
};
```

---

## Notes

1. **Max Width**: Semua panel dibatasi `maxWidth: 430px` untuk mobile-first design
2. **Safe Area**: Gunakan `env(safe-area-inset-*)` untuk iOS safe areas
3. **Animation**: Gunakan Framer Motion dengan ease curve `[0.32, 0.72, 0, 1]`
4. **Font**: Plus Jakarta Sans untuk semua text
5. **Color Usage**: Olive hanya untuk accent, BUKAN untuk text

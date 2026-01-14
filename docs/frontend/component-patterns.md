# MilkyHoop Frontend - Component Patterns & Conventions

## Overview

Dokumentasi ini menjelaskan patterns dan conventions yang dipakai di codebase MilkyHoop frontend.

---

## 1. File & Folder Structure

### Feature Modules
Setiap fitur besar punya folder sendiri di `src/components/app/`:

```
src/components/app/
├── PurchaseInvoice/
│   ├── index.tsx           # Main panel component
│   ├── SearchPage.tsx      # Search & filter subpage
│   └── CreateInvoice/      # Create flow
│       ├── index.tsx
│       ├── SummaryBar.tsx
│       └── FieldPill.tsx
├── Inventory/
│   └── index.tsx
├── SalesTransaction/
│   └── SalesTransaction.tsx
└── ...
```

### Naming Conventions
- **Folders**: PascalCase (`PurchaseInvoice/`, `ChatPanel/`)
- **Component files**: PascalCase (`SearchPage.tsx`, `BillCard.tsx`)
- **Utility files**: camelCase (`fetchWithAuth.ts`, `soundFeedback.ts`)
- **Type files**: camelCase (`purchaseInvoice.ts`, `inventory.ts`)

---

## 2. Component Structure Pattern

### Standard Component Template
```tsx
/**
 * ComponentName - Brief description
 * More details if needed
 */
import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchWithAuth } from '../../../utils/fetchWithAuth';

// ============================================================================
// DESIGN SYSTEM
// ============================================================================
const COLORS = {
  bgPrimary: '#FFFFFF',
  bgCard: '#F7F6F3',
  // ... (defined per-component, not centralized)
};

// ============================================================================
// TYPES
// ============================================================================
interface ComponentProps {
  isOpen: boolean;
  onClose: () => void;
  // ...
}

interface SomeData {
  id: string;
  // ...
}

// ============================================================================
// HELPERS
// ============================================================================
const formatSomething = (value: number): string => {
  // ...
};

// ============================================================================
// SUB-COMPONENTS
// ============================================================================
const SubComponent: React.FC<{ prop: string }> = ({ prop }) => {
  return <div>{prop}</div>;
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================
const ComponentName: React.FC<ComponentProps> = ({ isOpen, onClose }) => {
  // State
  const [data, setData] = useState<SomeData[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Refs
  const listRef = useRef<HTMLDivElement>(null);

  // Effects
  useEffect(() => {
    // ...
  }, []);

  // Handlers
  const handleClick = useCallback(() => {
    // ...
  }, []);

  // Render
  if (!isOpen) return null;

  return (
    <motion.div>
      {/* Content */}
    </motion.div>
  );
};

export default ComponentName;
```

### Section Separators
Gunakan comment blocks untuk memisahkan sections:
```tsx
// ============================================================================
// SECTION NAME
// ============================================================================
```

---

## 3. Styling Patterns

### Inline Styles (Primary Method)
```tsx
<div
  style={{
    padding: '14px 16px',
    background: COLORS.bgCard,
    borderRadius: 16,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  }}
>
```

### Design System Colors
Setiap komponen define COLORS object sendiri (tidak centralized):
```tsx
const COLORS = {
  // Backgrounds
  bgPrimary: '#FFFFFF',
  bgCard: '#F7F6F3',
  bgCardInner: '#EFEEE9',
  bgCardDarker: '#E8E7E2',
  bgHover: '#E2E1DC',

  // Text
  textPrimary: '#1A1A1A',
  textSecondary: '#6B6B6B',
  textMuted: '#9A9A9A',

  // Semantic
  success: '#5B8C51',
  warning: '#C9A227',
  danger: '#C45C4B',
  info: '#4A8DB7',

  // Borders
  borderColor: '#E8E6E1',
};
```

### Tailwind (Secondary)
Dipakai untuk utility classes tertentu:
```tsx
<nav className="fixed z-50 md:hidden">
```

### Hover States dengan useState
```tsx
const [hovered, setHovered] = useState(false);

<div
  onMouseEnter={() => setHovered(true)}
  onMouseLeave={() => setHovered(false)}
  style={{
    background: hovered ? COLORS.bgCardDarker : COLORS.bgCard,
    transition: 'background 0.15s ease',
  }}
>
```

---

## 4. Animation Patterns

### Page Transitions (Slide)
```tsx
<motion.div
  initial={{ x: '100%' }}
  animate={{ x: 0 }}
  exit={{ x: '100%' }}
  transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
>
```

### Auto-Hide on Scroll
```tsx
const [visible, setVisible] = useState(true);
const lastScrollY = useRef(0);

const handleScroll = (e: React.UIEvent<HTMLElement>) => {
  const scrollY = e.currentTarget.scrollTop;
  if (scrollY > lastScrollY.current && scrollY > 50) {
    setVisible(false);  // Scrolling down
  } else {
    setVisible(true);   // Scrolling up
  }
  lastScrollY.current = scrollY;
};

<motion.nav
  animate={{
    y: visible ? 0 : 100,
    opacity: visible ? 1 : 0,
  }}
  transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
>
```

### Accordion Expand/Collapse
```tsx
<AnimatePresence initial={false}>
  {isExpanded && (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.2, ease: [0.32, 0.72, 0, 1] }}
    >
      {/* Content */}
    </motion.div>
  )}
</AnimatePresence>
```

### Chevron Rotation
```tsx
<motion.span
  animate={{ rotate: isExpanded ? 180 : 0 }}
  transition={{ duration: 0.2 }}
>
  <ChevronIcon />
</motion.span>
```

---

## 5. Data Fetching Patterns

### Basic Fetch with Loading State
```tsx
const [data, setData] = useState<Item[]>([]);
const [isLoading, setIsLoading] = useState(true);
const [error, setError] = useState<string | null>(null);

const fetchData = useCallback(async (refresh = false) => {
  try {
    setIsLoading(true);
    setError(null);

    const response = await fetchWithAuth('/api/endpoint');
    if (!response.ok) throw new Error('Failed to fetch');

    const result = await response.json();
    setData(result.data);
  } catch (err) {
    setError(err instanceof Error ? err.message : 'Unknown error');
  } finally {
    setIsLoading(false);
  }
}, []);

useEffect(() => {
  fetchData();
}, [fetchData]);
```

### API Response Mapping
```tsx
// Map API response to frontend interface
const mapApiToFrontend = (apiItem: any): FrontendItem => ({
  id: apiItem.id,
  name: apiItem.display_name || apiItem.name,
  amount: apiItem.total_amount,
  status: normalizeStatus(apiItem.status),
});
```

---

## 6. Type Patterns

### Interface Definitions
```tsx
// Props interface
interface ComponentProps {
  isOpen: boolean;
  onClose: () => void;
  data?: SomeData[];
  isMobile?: boolean;
}

// Data interface
interface PurchaseInvoiceListItem {
  id: string;
  invoice_number: string;
  supplier_name: string;
  total_amount: number;
  status: 'paid' | 'partial' | 'unpaid' | 'overdue';
  due_date: string;
}

// Config object types
const statusConfig: Record<string, { label: string; color: string }> = {
  paid: { label: 'Lunas', color: COLORS.success },
  // ...
};
```

### Type Exports
```tsx
export type { PurchaseInvoiceListItem, SortField, SortOrder };
export type PaymentStatus = 'all' | 'paid' | 'partial' | 'unpaid' | 'overdue';
```

---

## 7. Icon Patterns

### Inline SVG Components
```tsx
const IconBack = () => (
  <svg width="24" height="24" fill="none" viewBox="0 0 24 24">
    <path
      d="M15 18l-6-6 6-6"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const IconPlus = () => (
  <svg width="24" height="24" fill="none" viewBox="0 0 24 24">
    <path
      d="M12 5v14M5 12h14"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </svg>
);
```

---

## 8. Mobile-First Patterns

### Safe Area Handling
```tsx
style={{
  paddingTop: 'env(safe-area-inset-top)',
  paddingBottom: 'max(100px, calc(100px + env(safe-area-inset-bottom)))',
  bottom: 'max(24px, env(safe-area-inset-bottom))',
}}
```

### Device Detection
```tsx
import { isDesktopBrowser, isMobileBrowser } from './utils/device';

// Conditional rendering
{isMobileBrowser() && <MobileComponent />}
{isDesktopBrowser() && <DesktopComponent />}

// Conditional in routes
const HomeRoute: React.FC = () => {
  if (isDesktopBrowser()) {
    return isAuthenticated() ? <Dashboard /> : <Navigate to="/login" />;
  }
  return isAuthenticated() ? <Dashboard /> : <HomePage />;
};
```

### Responsive Hiding
```tsx
<nav className="fixed z-50 md:hidden">
  {/* Only visible on mobile */}
</nav>
```

---

## 9. Common UI Components

### Pill/Chip Button
```tsx
const Pill: React.FC<{ active?: boolean; onClick?: () => void; children: React.ReactNode }> = ({
  active,
  onClick,
  children,
}) => (
  <button
    onClick={onClick}
    style={{
      padding: '6px 12px',
      borderRadius: 100,
      border: 'none',
      background: active ? COLORS.textPrimary : COLORS.bgCard,
      color: active ? COLORS.textInverse : COLORS.textSecondary,
      fontSize: 13,
      fontWeight: 500,
      cursor: onClick ? 'pointer' : 'default',
    }}
  >
    {children}
  </button>
);
```

### Card with Status Dot
```tsx
<div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
  {/* Status dot */}
  <span style={{
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: statusColor,
    flexShrink: 0,
    marginTop: 6,
  }} />

  {/* Content */}
  <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
    <div style={{ fontWeight: 600 }}>{title}</div>
    <div style={{ fontSize: 12, color: COLORS.textMuted }}>{subtitle}</div>
  </div>
</div>
```

---

## 10. Recommendations for Improvement

### Should Consider

1. **Centralized Design System**
   - Extract COLORS ke shared file (`src/styles/colors.ts`)
   - Create reusable UI primitives (`Button`, `Card`, `Badge`)

2. **Data Fetching Library**
   - Adopt React Query atau SWR untuk:
     - Automatic caching
     - Background refetching
     - Loading/error states

3. **State Management**
   - Consider Zustand untuk complex global state
   - Better than prop drilling

4. **Icon Library**
   - Adopt Lucide React atau Heroicons
   - Consistent sizing, tree-shakeable

5. **Form Library**
   - React Hook Form untuk complex forms
   - Built-in validation

### Keep As-Is

- Framer Motion untuk animations (good choice)
- TypeScript strict mode (good practice)
- Feature-based folder structure (scalable)
- Inline styles untuk one-off styling (acceptable tradeoff)

# MilkyHoop Frontend - Tech Stack Documentation

## Overview

MilkyHoop adalah aplikasi web React TypeScript untuk manajemen bisnis/akuntansi dengan fokus pada mobile-first design dan pengalaman pengguna yang smooth.

---

## 1. Core Framework & Build Tools

| Category | Technology | Version |
|----------|------------|---------|
| **Framework** | React | ^18.2.0 |
| **Language** | TypeScript | ^4.7.4 |
| **Build Tool** | Create React App (react-scripts) | 5.0.1 |
| **Package Manager** | npm | - |

### Configuration Files
- `tsconfig.json` - TypeScript config dengan `strict: true`
- `package.json` - Dependencies dan scripts

### Scripts
```bash
npm start      # Development server
npm run build  # Production build
npm test       # Run tests
```

---

## 2. Styling & UI

| Category | Technology | Notes |
|----------|------------|-------|
| **CSS Framework** | TailwindCSS | ^3.3.0, preflight disabled |
| **Primary Styling** | Inline Styles | Kebanyakan komponen pakai inline style objects |
| **UI Library** | Custom Components | Tidak pakai library eksternal (MUI, Chakra, dll) |
| **Icons** | Custom SVG Components | Inline SVG, tidak pakai library icon |
| **Fonts** | Plus Jakarta Sans | Via Google Fonts / system fonts |

### Design System Colors
```typescript
const COLORS = {
  bgPrimary: '#FFFFFF',
  bgCard: '#F7F6F3',
  bgCardInner: '#EFEEE9',
  bgCardDarker: '#E8E7E2',
  textPrimary: '#1A1A1A',
  textSecondary: '#6B6B6B',
  textMuted: '#9A9A9A',
  success: '#5B8C51',
  warning: '#C9A227',
  danger: '#C45C4B',
  info: '#4A8DB7',
  borderColor: '#E8E6E1',
};
```

### TailwindCSS Config
- `preflight: false` - Disable Tailwind reset, pakai custom reset
- Custom animations: `slide-up`, `popup-slide`, `fade-in`

---

## 3. State Management

| Category | Technology | Notes |
|----------|------------|-------|
| **Global State** | React Context | AuthContext untuk auth state |
| **Local State** | useState/useReducer | Standard React hooks |
| **Server State** | Native fetch | Tidak pakai React Query/SWR |
| **Form Handling** | Controlled Components | Native React, tanpa Formik/RHF |

### Context Structure
```
src/contexts/
├── AuthContext.tsx    # Authentication state
├── ThemeContext.tsx   # (kosong/placeholder)
└── WorkspaceContext.tsx # (kosong/placeholder)
```

---

## 4. Routing & Navigation

| Category | Technology | Version |
|----------|------------|---------|
| **Router** | React Router DOM | ^6.3.0 |

### Route Structure
```tsx
<Routes>
  <Route path="/login" element={<QRLoginPage />} />
  <Route path="/" element={<HomeRoute />} />
</Routes>
```

### Navigation Components
- `BottomNavigation.tsx` - Mobile bottom nav dengan auto-hide on scroll
- `NavSidebar/` - Desktop sidebar navigation
- `WorkspaceSidebar/` - Workspace switcher

### Auth Guards
- `LoginPageGuard` - Redirect authenticated users from login
- `HomeRoute` - Conditional render based on auth & device type

---

## 5. API & Data Fetching

| Category | Technology | Notes |
|----------|------------|-------|
| **HTTP Client** | Native fetch | Dengan wrapper `fetchWithAuth` |
| **Base URL** | `/api/` | Proxied ke backend via nginx |
| **Auth Method** | JWT Bearer Token | Stored di localStorage |
| **WebSocket** | Native WebSocket | Singleton pattern untuk device sync |

### API Utils
```typescript
// src/utils/fetchWithAuth.ts
export const fetchWithAuth = async (url: string, options: RequestInit = {}): Promise<Response> => {
  const token = localStorage.getItem('access_token');
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
};
```

### Auth Flow
1. QR Code scan di mobile → token dikirim ke desktop
2. Token disimpan di `localStorage` (access_token, refresh_token)
3. Auto-refresh ketika token hampir expired
4. Session sync via WebSocket

---

## 6. Animation & Interaction

| Category | Technology | Version |
|----------|------------|---------|
| **Animation Library** | Framer Motion | ^12.23.25 |
| **Gestures** | Framer Motion gestures | Swipe, drag, etc |
| **Transitions** | CSS + Framer Motion | Kombinasi keduanya |

### Common Animation Patterns
```tsx
// Page slide transition
<motion.div
  initial={{ x: '100%' }}
  animate={{ x: 0 }}
  exit={{ x: '100%' }}
  transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
>

// Auto-hide on scroll
animate={{
  y: visible ? 0 : 100,
  opacity: visible ? 1 : 0,
}}
```

---

## 7. Third-party Integrations

| Category | Technology | Usage |
|----------|------------|-------|
| **Barcode Scanning** | @zxing/library, html5-qrcode, jsqr | Multiple implementations |
| **Camera** | react-webcam | ^7.2.0 |
| **Fuzzy Search** | Fuse.js | ^7.1.0 |
| **Image Processing** | sharp | ^0.34.5 (dev) |

### Notable Absences
- ❌ Analytics (GA, Mixpanel)
- ❌ Error Tracking (Sentry)
- ❌ Payment Gateway
- ❌ Cloud Storage

---

## 8. Project Structure

```
src/
├── App.tsx                 # Root component, routing
├── index.tsx               # Entry point
├── index.css               # Global CSS (Tailwind imports)
│
├── components/             # UI Components
│   ├── BottomNavigation.tsx
│   ├── HomePage.tsx
│   ├── LoginModal.tsx
│   ├── QRLoginPage.tsx
│   ├── app/                # Feature modules
│   │   ├── ChatPanel/
│   │   ├── PurchaseInvoice/
│   │   ├── SalesTransaction/
│   │   ├── Inventory/
│   │   ├── WorkspaceSidebar/
│   │   └── ...
│   ├── features/           # Older feature structure
│   └── ui/                 # Reusable UI primitives
│
├── contexts/               # React Contexts
│   └── AuthContext.tsx
│
├── hooks/                  # Custom hooks
│   ├── useDashboardSummary.ts
│   ├── useForceLogout.ts
│   ├── useMediaQuery.ts
│   ├── useRemoteScanner.ts
│   └── ...
│
├── lib/                    # Singletons & utilities
│   ├── DeviceWebSocketSingleton.ts
│   └── identifiers.ts
│
├── pages/                  # Page-level components
│   └── Dashboard.tsx
│
├── services/               # API service modules
│
├── styles/                 # CSS files
│
├── types/                  # TypeScript type definitions
│   ├── purchaseInvoice.ts
│   ├── inventory.ts
│   ├── customer.ts
│   └── ...
│
└── utils/                  # Utility functions
    ├── api.ts
    ├── auth.ts
    ├── device.ts
    ├── fetchWithAuth.ts
    └── websocket.ts
```

---

## 9. TypeScript Configuration

| Setting | Value | Notes |
|---------|-------|-------|
| **Strict Mode** | `true` | Full strict checking enabled |
| **Target** | ES2016 | Modern JavaScript |
| **JSX** | react-jsx | React 17+ JSX transform |
| **Module** | CommonJS | Standard module system |

### Type Definition Patterns
```typescript
// src/types/purchaseInvoice.ts
export interface PurchaseInvoiceListItem {
  id: string;
  supplier_name: string;
  invoice_number: string;
  total_amount: number;
  status: 'draft' | 'pending' | 'paid' | 'overdue';
  // ...
}
```

---

## 10. Testing

| Category | Technology | Status |
|----------|------------|--------|
| **Test Runner** | Jest (via CRA) | Configured |
| **Component Testing** | React Testing Library | ^13.3.0 |
| **User Event** | @testing-library/user-event | ^13.5.0 |
| **E2E Testing** | - | Not configured |

### Test Scripts
```bash
npm test           # Run tests in watch mode
npm test -- --coverage  # Run with coverage
```

---

## Environment & Deployment

### Development
```bash
npm start          # http://localhost:3000
```

### Production
```bash
cd /root/milkyhoop/frontend/web && ./deploy-prod.sh
```

### Docker
- Container: `milkyhoop-frontend-1`
- Port: 3000 (internal) → nginx proxy
- Domain: https://milkyhoop.com

---

## Summary

MilkyHoop frontend adalah **React + TypeScript** app yang relatif simpel dalam hal dependencies:
- **Tidak pakai** state management library (Redux, Zustand)
- **Tidak pakai** UI component library (MUI, Chakra)
- **Tidak pakai** data fetching library (React Query, SWR)
- **Kebanyakan** styling pakai inline styles, bukan CSS classes

Pendekatan ini membuat bundle size kecil tapi membutuhkan lebih banyak boilerplate code.

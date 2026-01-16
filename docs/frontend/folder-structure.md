# Frontend Folder Structure

> Last updated: 2026-01-16

## Overview

```
frontend/web/src/
├── components/
│   ├── app/          # Main application modules
│   ├── features/     # Feature-specific components
│   └── ui/           # Reusable UI primitives
├── contexts/         # React context providers
├── hooks/            # Custom React hooks
├── lib/              # Utility libraries
├── pages/            # Page components (routing)
├── services/         # API service layer
├── styles/           # Global styles
├── types/            # TypeScript type definitions
└── utils/            # Utility functions
```

## App Components (`components/app/`)

```
components/app/
├── ChatPanel/                    # Main chat interface & dashboard
│   ├── DashboardPanel/           # Dashboard with financial summaries
│   │   ├── AlertBanner.tsx
│   │   ├── BookmarkTab.tsx
│   │   ├── CashFlowChart.tsx
│   │   ├── HutangPanel.tsx
│   │   ├── KasBankPanel.tsx
│   │   ├── LabaRugiPanel.tsx
│   │   ├── PiutangPanel.tsx
│   │   ├── QuickCreateSection.tsx
│   │   ├── SummaryCards.tsx
│   │   ├── TopExpensesSection.tsx
│   │   └── index.tsx
│   ├── ActionMenuButton.tsx
│   ├── ActionMenuSheet.tsx
│   ├── ChatHeader.tsx
│   ├── ChatInput.tsx
│   ├── ExpenseForm.tsx
│   ├── ExpenseReasonForm.tsx
│   ├── MessageBubble.tsx
│   ├── MessageList.tsx
│   ├── ProductAutocomplete.tsx
│   ├── PurchaseForm.tsx
│   ├── SemuaModulPage.tsx
│   ├── SupplierAutocomplete.tsx
│   ├── actionMenuConstants.ts
│   ├── index.tsx
│   ├── useEdgeSwipe.ts
│   └── useModuleCustomization.ts
│
├── Items/                        # Master data items (goods & services)
│   ├── AddItemForm/
│   │   ├── sheets/               # Bottom sheets for selections
│   │   └── index.tsx
│   ├── ItemListPage/             # Item list with pagination
│   │   ├── components/
│   │   │   ├── ItemCard.tsx
│   │   │   ├── Pagination.tsx
│   │   │   ├── QuickFilters.tsx
│   │   │   ├── SearchBarTrigger.tsx
│   │   │   └── index.ts
│   │   ├── ItemListPage.tsx
│   │   └── index.ts
│   ├── ItemSearchPage/           # Item search & filter
│   │   ├── components/
│   │   │   ├── SearchFilters.tsx
│   │   │   ├── SearchResultCard.tsx
│   │   │   ├── SortSheet.tsx
│   │   │   └── index.ts
│   │   ├── ItemSearchPage.tsx
│   │   └── index.ts
│   ├── constants/
│   │   └── itemList.constants.ts
│   ├── hooks/
│   │   ├── useItemList.ts
│   │   ├── useItemSearch.ts
│   │   └── index.ts
│   ├── shared/
│   │   ├── ItemIcon.tsx
│   │   ├── ItemPriceDisplay.tsx
│   │   └── index.ts
│   ├── types/
│   │   └── itemList.types.ts
│   ├── ItemsPanel.tsx
│   └── index.ts
│
├── Inventory/                    # Stock management (track_inventory=true)
│   ├── AddProductForm.tsx
│   ├── AsetTetapForm.tsx
│   ├── InventoryPanel.tsx
│   ├── ProductCard.tsx
│   ├── ProductListItem.tsx
│   ├── ReturForm.tsx
│   ├── StockAdjustmentForm.tsx
│   └── index.ts
│
├── PurchaseInvoice/              # Purchase invoice management
│   ├── CreateInvoice/
│   │   ├── hooks/
│   │   │   └── useInvoiceForm.ts
│   │   ├── sheets/
│   │   │   ├── AttachmentSheet.tsx
│   │   │   ├── BottomSheet.tsx
│   │   │   ├── DiscountSheet.tsx
│   │   │   ├── InvoiceDiscountSheet.tsx
│   │   │   ├── ItemFormSheet.tsx
│   │   │   ├── SimpleInputSheet.tsx
│   │   │   └── VendorSheet.tsx
│   │   ├── styles/
│   │   │   └── tokens.ts
│   │   ├── CreateInvoiceHeader.tsx
│   │   ├── FieldPill.tsx
│   │   ├── ItemCard.tsx
│   │   ├── ItemsSection.tsx
│   │   ├── SummaryBar.tsx
│   │   ├── ToggleField.tsx
│   │   └── index.tsx
│   ├── FilterModal.tsx
│   ├── PurchaseInvoiceItem.tsx
│   ├── SearchPage.tsx
│   ├── SortModal.tsx
│   └── index.tsx
│
├── SalesInvoice/                 # Sales invoice management
│   ├── FilterModal.tsx
│   ├── SalesInvoiceItem.tsx
│   ├── SearchPage.tsx
│   ├── SortModal.tsx
│   └── index.tsx
│
├── SalesTransaction/             # POS / Sales
│   ├── CartSection.tsx
│   ├── PaymentSection.tsx
│   ├── RecentProducts.tsx
│   ├── SalesTransaction.tsx
│   ├── ScannerSection.tsx
│   ├── index.tsx
│   └── types.ts
│
├── Customer/                     # Customer management
│   ├── CustomerPanel.tsx
│   └── index.ts
│
├── Vendor/                       # Vendor/supplier management
│   ├── hooks/
│   │   ├── useVendorFilters.ts
│   │   └── useVendorSort.ts
│   ├── VendorBadges.tsx
│   ├── VendorCard.tsx
│   ├── VendorDetailSection.tsx
│   ├── VendorEmptyState.tsx
│   ├── VendorPanel.tsx
│   ├── VendorQuickActions.tsx
│   ├── VendorSearchPage.tsx
│   ├── VendorSearchResult.tsx
│   ├── VendorSortSheet.tsx
│   ├── VendorSummaryBar.tsx
│   ├── constants.ts
│   └── index.tsx
│
├── Expenses/                     # Expense list & search
│   ├── ExpenseItem.tsx
│   ├── FilterModal.tsx
│   ├── SearchPage.tsx
│   ├── SortModal.tsx
│   ├── index.tsx
│   └── types.ts
│
├── Debt/                         # Debt & receivables management
│   ├── DebtPanel.tsx
│   ├── HutangBankForm.tsx
│   ├── HutangGajiForm.tsx
│   ├── HutangPajakForm.tsx
│   ├── HutangUsahaForm.tsx
│   ├── PiutangUsahaForm.tsx
│   ├── UangMukaPelangganForm.tsx
│   ├── UangMukaPembelianForm.tsx
│   └── index.ts
│
├── KasBank/                      # Cash & bank management
│   ├── BebanDibayarDiMukaForm.tsx
│   ├── KasBankPanel.tsx
│   ├── ModalPriveForm.tsx
│   ├── PendapatanBungaForm.tsx
│   ├── PendapatanLainForm.tsx
│   └── index.ts
│
├── Insight/                      # Business insights & analytics
│   ├── InsightPanel.tsx
│   └── index.ts
│
├── Beban/                        # Expense tracking
│   └── index.tsx
│
├── Pembelian/                    # Purchase transactions
│   └── index.tsx
│
├── MoreModules/                  # Additional modules menu
│   └── index.tsx
│
├── NavSidebar/                   # Desktop navigation sidebar
│   ├── NavIcon.tsx
│   ├── UserMenu.tsx
│   └── index.tsx
│
├── WorkspaceSidebar/             # Workspace/tenant switcher
│   ├── MilkyAssistant.tsx
│   ├── SearchBar.tsx
│   ├── WorkspaceItem.tsx
│   ├── WorkspaceList.tsx
│   └── index.tsx
│
├── TenantSettingsSidebar/        # Tenant settings panel
│   └── index.tsx
│
├── AppLayout.tsx                 # Main app layout wrapper
└── ANIMATION_GUIDE.md            # Animation guidelines
```

## Feature Components (`components/features/`)

```
components/features/
├── auth/                         # Authentication components
│   ├── LoginForm.tsx
│   ├── ProtectedRoute.tsx
│   └── SignupForm.tsx
│
├── chat/                         # Chat feature components
│   ├── FileUpload/
│   │   ├── FileAttachment.tsx
│   │   ├── FilePreview.tsx
│   │   └── FileUploadButton.tsx
│   ├── MessageTypes/
│   │   ├── FinancialReport.tsx
│   │   ├── ImageMessage.tsx
│   │   └── TextMessage.tsx
│   ├── QuickActions/
│   │   ├── QuickActionBar.tsx
│   │   └── QuickActionButton.tsx
│   └── TypingIndicator.tsx
│
├── collaborator/                 # Team collaboration
│   ├── CollaboratorList.tsx
│   ├── InviteCollaborator.tsx
│   └── PermissionMatrix.tsx
│
├── tenant-public/                # Public tenant pages
│   ├── FloatingChatButton.tsx
│   ├── GuestChatModal.tsx
│   ├── TenantHero.tsx
│   └── TenantPublicPage.tsx
│
└── workspace/                    # Workspace management
    ├── CreateWorkspace.tsx
    ├── UpgradePrompt.tsx
    ├── WorkspaceSelector.tsx
    └── WorkspaceSettings.tsx
```

## UI Components (`components/ui/`)

```
components/ui/
├── Avatar.tsx                    # User avatars
├── Badge.tsx                     # Status badges
├── Button.tsx                    # Button variants
├── Card.tsx                      # Card container
├── CardGrid.tsx                  # Grid layout for cards
├── CardRow.tsx                   # Row layout for cards
├── Dropdown.tsx                  # Dropdown menu
├── EditableValue.tsx             # Inline editable values
├── Icon.tsx                      # Icon wrapper
├── Modal.tsx                     # Modal dialog
├── RemoteScanButton.tsx          # Remote barcode scan trigger
├── Spinner.tsx                   # Loading spinner
├── index.ts                      # Barrel exports
├── types.ts                      # UI type definitions
└── README.md                     # UI component docs
```

## Services (`services/`)

```
services/
├── auth/
│   └── auth.service.ts           # Authentication API
├── chat/
│   └── message.service.ts        # Chat message API
├── collaborator/
│   └── collaborator.service.ts   # Team member API
├── transaction/
│   └── transaction.service.ts    # Transaction API
└── workspace/
    └── workspace.service.ts      # Workspace API
```

## Contexts (`contexts/`)

```
contexts/
├── AuthContext.tsx               # Authentication state provider
├── ThemeContext.tsx              # Theme/dark mode provider
└── WorkspaceContext.tsx          # Current workspace provider
```

## Types (`types/`)

```
types/
├── items.ts              # Items master data (goods/services)
├── inventory.ts          # Stock & inventory
├── purchaseInvoice.ts    # Purchase invoices
├── salesInvoice.ts       # Sales invoices
├── purchase.ts           # Purchase transactions
├── transaction.ts        # General transactions
├── expense.ts            # Expenses
├── customer.ts           # Customers
├── vendor.ts             # Vendors/suppliers
├── debt.ts               # Debt management
├── hutangBank.ts         # Bank loans
├── hutangGaji.ts         # Salary payables
├── hutangPajak.ts        # Tax payables
├── hutangUsaha.ts        # Trade payables
├── piutangUsaha.ts       # Trade receivables
├── uangMuka.ts           # Advances
├── kasbank.ts            # Cash & bank
├── pendapatan.ts         # Income
├── retur.ts              # Returns
├── asetTetap.ts          # Fixed assets
├── bebanDibayarDiMuka.ts # Prepaid expenses
├── modalPrive.ts         # Owner's equity
├── laporanKeuangan.ts    # Financial reports
├── insight.ts            # Business insights
├── message.ts            # Chat messages
├── workspace.ts          # Workspaces/tenants
├── collaborator.ts       # Team collaborators
└── user.ts               # User authentication
```

## Utils (`utils/`)

```
utils/
├── fetchWithAuth.ts      # Authenticated API calls
├── api.ts                # API client
├── auth.ts               # Authentication helpers
├── formatters.ts         # Number/date formatters
├── validators.ts         # Input validation
├── helpers.ts            # General helpers
├── constants.ts          # App constants
├── device.ts             # Device detection
├── BarcodeScanner.ts     # Barcode scanning
├── SmartOverpayV6.ts     # Smart payment calculation
├── soundFeedback.ts      # Audio feedback
└── websocket.ts          # WebSocket client
```

## Hooks (`hooks/`)

```
hooks/
├── useAuth.ts              # Authentication state
├── useWorkspace.ts         # Current workspace
├── useChatMessages.ts      # Chat message handling
├── useCollaborators.ts     # Team members
├── useDashboardSummary.ts  # Dashboard data
├── useFileUpload.ts        # File upload handling
├── useRemoteScanner.ts     # Remote barcode scanning
├── useRemoteScanRequest.ts # Scan request handling
├── useSessionSync.ts       # Multi-tab session sync
├── useForceLogout.ts       # Force logout handling
├── useMediaQuery.ts        # Responsive breakpoints
├── useMobileBodyScroll.ts  # Mobile scroll control
└── useAutoExpandTextarea.ts # Auto-expanding textareas
```

## Lib (`lib/`)

```
lib/
├── DeviceWebSocketSingleton.ts   # WebSocket singleton instance
└── identifiers.ts                # ID generation utilities
```

## Pages (`pages/`)

```
pages/
├── Dashboard.tsx           # Main dashboard
├── LandingPage.tsx         # Public landing
├── LoginPage.tsx           # Login
├── SignupPage.tsx          # Registration
├── SettingsPage.tsx        # User settings
├── TenantLanding.tsx       # Tenant public page
├── SalesScanPage.tsx       # Barcode scanning
├── PembelianPage1.tsx      # Purchase flow step 1
├── PembelianPage2.tsx      # Purchase flow step 2
└── UITestPage.tsx          # Component testing
```

## Styles (`styles/`)

```
styles/
├── reset.css               # CSS reset
└── tokens.ts               # Design tokens
```

## Module Relationships

```
Items (Master Data)
    │
    ├── track_inventory = true ──► Inventory (Stock Management)
    │                                  ├── Stock adjustment
    │                                  ├── Stock opname
    │                                  └── Retur handling
    │
    ├── item_type = 'goods' ─────► PurchaseInvoice ──► Vendor
    │                              SalesInvoice ────► Customer
    │                              SalesTransaction
    │
    └── item_type = 'service' ───► PurchaseInvoice (no stock)
                                   SalesInvoice (no stock)
                                   SalesTransaction (no stock)
```

## Architecture Layers

```
┌─────────────────────────────────────────────────────┐
│                     Pages                            │
│  (Dashboard, Login, Settings, etc.)                 │
├─────────────────────────────────────────────────────┤
│                  App Components                      │
│  (ChatPanel, Items, PurchaseInvoice, Vendor, etc.)  │
├─────────────────────────────────────────────────────┤
│               Feature Components                     │
│  (auth, chat, collaborator, workspace)              │
├─────────────────────────────────────────────────────┤
│                  UI Components                       │
│  (Button, Card, Modal, Avatar, etc.)                │
├─────────────────────────────────────────────────────┤
│     Contexts          │        Hooks                │
│  (Auth, Theme,        │  (useAuth, useWorkspace,    │
│   Workspace)          │   useChatMessages, etc.)    │
├─────────────────────────────────────────────────────┤
│                    Services                          │
│  (auth, chat, collaborator, transaction, workspace) │
├─────────────────────────────────────────────────────┤
│         Utils              │        Lib             │
│  (api, formatters,         │  (WebSocket,           │
│   validators, etc.)        │   identifiers)         │
└─────────────────────────────────────────────────────┘
```

## Design Patterns

1. **Panel Pattern**: Full-screen modals with header, content, FAB
2. **Form Pattern**: Fullscreen forms with bottom sheets for selections
3. **List Pattern**: Scrollable lists with pull-to-refresh, infinite scroll
4. **Sheet Pattern**: Bottom sheets for quick selections/inputs
5. **Service Pattern**: Dedicated service files for API calls per domain

See [component-patterns.md](./component-patterns.md) for detailed patterns.

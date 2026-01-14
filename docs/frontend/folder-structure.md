# Frontend Folder Structure

> Last updated: 2026-01-14

## Overview

```
frontend/web/src/
├── components/
│   ├── app/          # Main application components (modules)
│   └── ui/           # Reusable UI primitives
├── types/            # TypeScript type definitions
├── utils/            # Utility functions
├── hooks/            # Custom React hooks
├── pages/            # Next.js pages (routing)
└── styles/           # Global styles
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
│   │   └── index.tsx             # Fullscreen form (1068 lines)
│   ├── ItemsPanel.tsx            # List view with filters
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

## Types (`types/`)

```
types/
├── items.ts              # Items master data (goods/services)
├── inventory.ts          # Stock & inventory
├── purchaseInvoice.ts    # Purchase invoices
├── purchase.ts           # Purchase transactions
├── transaction.ts        # General transactions
├── expense.ts            # Expenses
├── customer.ts           # Customers
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

## Module Relationships

```
Items (Master Data)
    │
    ├── track_inventory = true ──► Inventory (Stock Management)
    │                                  ├── Stock adjustment
    │                                  ├── Stock opname
    │                                  └── Retur handling
    │
    ├── item_type = 'goods' ─────► PurchaseInvoice
    │                              SalesTransaction
    │
    └── item_type = 'service' ───► PurchaseInvoice (no stock)
                                   SalesTransaction (no stock)
```

## Design Patterns

1. **Panel Pattern**: Full-screen modals with header, content, FAB
2. **Form Pattern**: Fullscreen forms with bottom sheets for selections
3. **List Pattern**: Scrollable lists with pull-to-refresh, infinite scroll
4. **Sheet Pattern**: Bottom sheets for quick selections/inputs

See [component-patterns.md](./component-patterns.md) for detailed patterns.

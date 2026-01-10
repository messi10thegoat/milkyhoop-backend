import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { sendTenantMessage, fetchChatHistory } from '../../../utils/api';
import { isAuthenticated, getUserId } from '../../../utils/auth';
import ActionMenuButton from './ActionMenuButton';
import PurchaseForm from './PurchaseForm';
import ExpenseForm from './ExpenseForm';
import Beban from '../Beban';
import ProductAutocomplete from './ProductAutocomplete';
import SupplierAutocomplete from './SupplierAutocomplete';
import BarcodeRegistrationModal from '../../BarcodeRegistrationModal';
import SalesTransaction from '../SalesTransaction';
import Pembelian from '../Pembelian';
import { InventoryPanel } from '../Inventory';
import { KasBankPanel } from '../KasBank';
import { DebtPanel } from '../Debt';
import { CustomerPanel } from '../Customer';
import { InsightPanel } from '../Insight';
import { useIsDesktop } from '../../../hooks/useMediaQuery';
import DashboardPanel from './DashboardPanel';
import PurchaseInvoiceForm from './PurchaseInvoiceForm';
import PurchaseInvoicePanel from '../PurchaseInvoice';
// Slide animation for mobile modals (WhatsApp style - 300ms)
const slideTransition = {
  duration: 0.3,
  ease: [0.4, 0, 0.2, 1] as [number, number, number, number] // Material Design easing
};

interface Message {
  id: string;
  sender: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

interface Tenant {
  id: string;
  name: string;
  avatar?: string;
  status?: 'online' | 'offline';
}

// Panel types for split view
type PanelType = 'none' | 'pos' | 'inventory' | 'kasbank' | 'debt' | 'customer' | 'insight' | 'purchase' | 'expense' | 'pembelian' | 'settings';

interface ChatPanelProps {
  activeTenant: Tenant | null;
  onBack: () => void;
  isMobile?: boolean;
  onPanelToggle?: (panel: PanelType) => void;  // Unified callback for all panels
  activePanel?: PanelType;  // Current active panel from Dashboard
  externalTransactionMessage?: string | null;  // For Desktop mode: receive transaction from sibling SalesTransaction
  // HYBRID CONVERSATIONAL POS: Callback to pass prefill data to Dashboard
  onPOSPrefillData?: (data: { items?: Array<{productQuery: string; qty: number; unit?: string}>; paymentMethod?: string } | null) => void;
}

// Detect if device is mobile (iOS, Android, or small screen)
const isMobileDevice = () => {
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
    || window.innerWidth < 768;
};

// Typing indicator component - Milkyhoop style
const TypingIndicator: React.FC = () => {
  return (
    <div className="flex gap-1">
      <span
        className="w-2 h-2 rounded-full"
        style={{
          backgroundColor: '#9A9A9A',
          animation: 'typing 1.4s infinite',
          animationDelay: '0ms',
        }}
      />
      <span
        className="w-2 h-2 rounded-full"
        style={{
          backgroundColor: '#9A9A9A',
          animation: 'typing 1.4s infinite',
          animationDelay: '200ms',
        }}
      />
      <span
        className="w-2 h-2 rounded-full"
        style={{
          backgroundColor: '#9A9A9A',
          animation: 'typing 1.4s infinite',
          animationDelay: '400ms',
        }}
      />
    </div>
  );
};








const ChatPanel: React.FC<ChatPanelProps> = ({ activeTenant, onBack, isMobile = false, onPanelToggle, activePanel = 'none', externalTransactionMessage, onPOSPrefillData }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const isDesktop = useIsDesktop(); // Desktop: ≥1024px
  const [message, setMessage] = useState('');
  const [textareaHeight, setTextareaHeight] = useState(20); // For dynamic pill border radius
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  // Pagination state for WhatsApp-style load more
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  // NOTE: useRef is intentional here.
  // This flag guards scroll side-effects and MUST NOT trigger re-render.
  // Do NOT convert this to useState - it will cause race condition with scrollToBottom effect.
  const isLoadingOlderMessagesRef = useRef(false);
  const MESSAGES_PER_PAGE = 10;
  const [isTyping, setIsTyping] = useState(false);
  const [isPurchaseFormOpen, setIsPurchaseFormOpen] = useState(false);
  // Barcode registration modal state
  const [barcodeModalOpen, setBarcodeModalOpen] = useState(false);
  const [barcodeProductId, setBarcodeProductId] = useState('');
  const [barcodeProductName, setBarcodeProductName] = useState('');
  // Product data from barcode scan (including auto-fill fields)
  const [scannedProductData, setScannedProductData] = useState<{
    name: string;
    barcode: string;
    unit?: string;
    units_per_pack?: number;
    harga_jual?: number;
    last_price?: number;
  } | null>(null);
  // Mobile panel persistence helper
  const getMobileActivePanel = () => {
    if (typeof window !== 'undefined' && window.innerWidth < 768) {
      return localStorage.getItem('mobile_active_panel') || 'none';
    }
    return 'none';
  };

  // Beban (expense) state - with persistence
  const [isExpenseFormOpen, setIsExpenseFormOpen] = useState(() => getMobileActivePanel() === 'expense');

  // Sales transaction state (only used for mobile full-screen modal)
  const [isSalesOpen, setIsSalesOpen] = useState(() => getMobileActivePanel() === 'pos');
  // Pembelian (purchase) state
  const [isPembelianOpen, setIsPembelianOpen] = useState(() => getMobileActivePanel() === 'pembelian');
  // Inventory panel state
  const [isInventoryOpen, setIsInventoryOpen] = useState(() => getMobileActivePanel() === 'inventory');
  // Kas & Bank panel state
  const [isKasBankOpen, setIsKasBankOpen] = useState(() => getMobileActivePanel() === 'kasbank');
  // Hutang/Piutang panel state
  const [isDebtOpen, setIsDebtOpen] = useState(() => getMobileActivePanel() === 'debt');
  // Pelanggan panel state
  const [isCustomerOpen, setIsCustomerOpen] = useState(() => getMobileActivePanel() === 'customer');
  // Insight panel state
  const [isInsightOpen, setIsInsightOpen] = useState(() => getMobileActivePanel() === 'insight');
  // Action menu state (controlled mode for edge swipe gesture)
  const [isActionMenuOpen, setIsActionMenuOpen] = useState(false);
  // Dashboard panel state (edge swipe + bookmark button)
  const [isDashboardOpen, setIsDashboardOpen] = useState(false);
  // PurchaseInvoice panel & form state (Faktur Pembelian)
  const [isPurchaseInvoiceListOpen, setIsPurchaseInvoiceListOpen] = useState(false);
  const [isPurchaseInvoiceFormOpen, setIsPurchaseInvoiceFormOpen] = useState(false);
  // Legacy state for direct form access (deprecated)
  const [isPurchaseInvoiceOpen, setIsPurchaseInvoiceOpen] = useState(false);

  // Persist mobile panel state to localStorage
  useEffect(() => {
    // Only persist on mobile
    if (typeof window === 'undefined' || window.innerWidth >= 768) return;

    // Determine which panel is active
    let activePanel = 'none';
    if (isSalesOpen) activePanel = 'pos';
    else if (isPembelianOpen) activePanel = 'pembelian';
    else if (isExpenseFormOpen) activePanel = 'expense';
    else if (isInventoryOpen) activePanel = 'inventory';
    else if (isKasBankOpen) activePanel = 'kasbank';
    else if (isDebtOpen) activePanel = 'debt';
    else if (isCustomerOpen) activePanel = 'customer';
    else if (isInsightOpen) activePanel = 'insight';

    localStorage.setItem('mobile_active_panel', activePanel);
  }, [isSalesOpen, isPembelianOpen, isExpenseFormOpen, isInventoryOpen, isKasBankOpen, isDebtOpen, isCustomerOpen, isInsightOpen]);

  // ========== HYBRID CONVERSATIONAL POS: Prefill data from chat action ==========
  const [posPrefillData, setPOSPrefillData] = useState<{
    items?: Array<{productQuery: string; qty: number; unit?: string}>;
    paymentMethod?: string;
  } | null>(null);

  // Check if we need to re-open PurchaseForm modal (returning from camera)
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const action = params.get('action');

    if (action === 'openPurchaseForm') {
      // Check if returning from barcode scan with product data
      const state = location.state as any;
      console.log('[ChatPanel] openPurchaseForm action, state:', state);

      if (state?.action === 'purchase' && state?.productData) {
        console.log('[ChatPanel] Setting scannedProductData:', state.productData);
        setScannedProductData(state.productData);
      }

      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('purchase');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsPurchaseFormOpen(true);
      }
      // Clear the action from URL and state
      params.delete('action');
      const newSearch = params.toString();
      navigate(location.pathname + (newSearch ? '?' + newSearch : ''), { replace: true, state: {} });
    }
  }, [location.search, location.pathname, navigate, location.state]);

  // Handle camera button click - open POS directly (not Purchase form)
  const handleCameraClick = () => {
    // Desktop: Use lifted state from Dashboard for split view
    if (isDesktop && onPanelToggle) {
      onPanelToggle('pos');
    } else {
      // Mobile: Use local state for full-screen modal
      setIsSalesOpen(true);
    }
  };
  // Inline autocomplete state
  const [showProductAutocomplete, setShowProductAutocomplete] = useState(false);
  const [showSupplierAutocomplete, setShowSupplierAutocomplete] = useState(false);
  const [showUnitDropdown, setShowUnitDropdown] = useState(false);
  const [showPaymentDropdown, setShowPaymentDropdown] = useState(false);
  const [productQuery, setProductQuery] = useState('');
  const [supplierQuery, setSupplierQuery] = useState('');
  const [detectedUnit, setDetectedUnit] = useState('');
  const lastInputRef = useRef('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Edge swipe gesture is now handled inside DashboardPanel component

  // Handle action menu item click
  const handleActionMenuClick = (itemId: string) => {
    console.log('[ChatPanel] Action menu item clicked:', itemId);
    if (itemId === 'purchase_goods') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('pembelian');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsPembelianOpen(true);
      }
    } else if (itemId === 'sales_product') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('pos');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsSalesOpen(true);
      }
    } else if (itemId === 'expense') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('expense');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsExpenseFormOpen(true);
      }
    } else if (itemId === 'inventory_update') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('inventory');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsInventoryOpen(true);
      }
    } else if (itemId === 'cash_bank') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('kasbank');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsKasBankOpen(true);
      }
    } else if (itemId === 'debt_receivable') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('debt');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsDebtOpen(true);
      }
    } else if (itemId === 'customer') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('customer');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsCustomerOpen(true);
      }
    } else if (itemId === 'insight') {
      // Desktop: Use lifted state from Dashboard for split view
      if (isDesktop && onPanelToggle) {
        onPanelToggle('insight');
      } else {
        // Mobile: Use local state for full-screen modal
        setIsInsightOpen(true);
      }
    } else if (itemId === 'dashboard') {
      // Open Dashboard panel with slide-in animation from right
      setIsDashboardOpen(true);
    } else if (itemId === 'stock_purchase') {
      // Open Purchase Invoice list panel (Faktur Pembelian)
      setIsPurchaseInvoiceListOpen(true);
    }
  };

  // Handle Dashboard Quick Create action clicks
  const handleDashboardActionClick = useCallback((action: string) => {
    console.log('[ChatPanel] Dashboard action clicked:', action);
    setIsDashboardOpen(false); // Close Dashboard first

    // TODO: Implement these actions when panels are ready
    // For now, log the action - these will open their respective panels
    switch (action) {
      case 'invoice':
        console.log('[ChatPanel] Opening Invoice panel (not yet implemented)');
        break;
      case 'terima_bayar':
        console.log('[ChatPanel] Opening Terima Bayar panel (not yet implemented)');
        break;
      case 'estimate':
        console.log('[ChatPanel] Opening Estimate panel (not yet implemented)');
        break;
      case 'credit_memo':
        console.log('[ChatPanel] Opening Credit Memo panel (not yet implemented)');
        break;
      case 'bayar_hutang':
        console.log('[ChatPanel] Opening Bayar Hutang panel (not yet implemented)');
        break;
      case 'vendor_credit':
        console.log('[ChatPanel] Opening Vendor Credit panel (not yet implemented)');
        break;
      case 'transfer':
        // Open Kas & Bank panel for transfer
        if (isDesktop && onPanelToggle) {
          onPanelToggle('kasbank');
        } else {
          setIsKasBankOpen(true);
        }
        break;
      case 'setor_tunai':
        // Open Kas & Bank panel for setor tunai
        if (isDesktop && onPanelToggle) {
          onPanelToggle('kasbank');
        } else {
          setIsKasBankOpen(true);
        }
        break;
      case 'tarik_tunai':
        // Open Kas & Bank panel for tarik tunai
        if (isDesktop && onPanelToggle) {
          onPanelToggle('kasbank');
        } else {
          setIsKasBankOpen(true);
        }
        break;
      case 'jurnal_umum':
        console.log('[ChatPanel] Opening Jurnal Umum panel (not yet implemented)');
        break;
      case 'stock_adjust':
        // Open Inventory panel for stock adjustment
        if (isDesktop && onPanelToggle) {
          onPanelToggle('inventory');
        } else {
          setIsInventoryOpen(true);
        }
        break;
    }
  }, [isDesktop, onPanelToggle]);

  // Handle Dashboard summary card clicks
  const handleSummaryCardClick = useCallback((cardType: 'labarugi' | 'piutang' | 'hutang' | 'kasbank') => {
    console.log('[ChatPanel] Summary card clicked:', cardType);
    setIsDashboardOpen(false); // Close Dashboard first

    switch (cardType) {
      case 'labarugi':
        // Open Insight panel for Laba Rugi
        if (isDesktop && onPanelToggle) {
          onPanelToggle('insight');
        } else {
          setIsInsightOpen(true);
        }
        break;
      case 'piutang':
      case 'hutang':
        // Open Debt panel
        if (isDesktop && onPanelToggle) {
          onPanelToggle('debt');
        } else {
          setIsDebtOpen(true);
        }
        break;
      case 'kasbank':
        // Open Kas & Bank panel
        if (isDesktop && onPanelToggle) {
          onPanelToggle('kasbank');
        } else {
          setIsKasBankOpen(true);
        }
        break;
    }
  }, [isDesktop, onPanelToggle]);

  // Handle barcode registration clicks from receipt HTML
  useEffect(() => {
    const handleBarcodeClick = (e: Event) => {
      const target = e.target as HTMLElement;
      console.log('[ChatPanel] Click detected on:', target.tagName, target.className);

      if (target.classList.contains('barcode-register-btn')) {
        e.preventDefault();
        e.stopPropagation();

        const productId = target.getAttribute('data-product-id');
        const productName = target.getAttribute('data-product-name');

        console.log('[ChatPanel] Barcode button clicked! ProductId:', productId, 'ProductName:', productName);

        // Check if productId and productName are valid (not null, not empty, not "None")
        if (productId && productName && productId.trim() !== '' && productId !== 'None' && productName.trim() !== '') {
          console.log('[ChatPanel] Opening barcode modal...');
          setBarcodeProductId(productId);
          setBarcodeProductName(productName);
          setBarcodeModalOpen(true);
        } else {
          console.error('[ChatPanel] Missing or invalid productId/productName:', { productId, productName });
          alert('⚠️ Product ID tidak tersedia.\n\nFitur registrasi barcode hanya tersedia untuk produk yang sudah terdaftar di sistem.\n\nSaran: Produk ini kemungkinan baru pertama kali dibeli. Lakukan pembelian lagi untuk produk yang sama, maka barcode bisa didaftarkan.');
        }
      }
    };

    // Add event listener to messages container
    const container = messagesContainerRef.current;
    if (container) {
      console.log('[ChatPanel] Adding barcode click listener to container');
      container.addEventListener('click', handleBarcodeClick);
    } else {
      console.warn('[ChatPanel] Messages container ref not available');
    }

    return () => {
      if (container) {
        console.log('[ChatPanel] Removing barcode click listener');
        container.removeEventListener('click', handleBarcodeClick);
      }
    };
  }, []);

  // Handle barcode registration success
  const handleBarcodeSuccess = (barcode: string) => {
    console.log('[ChatPanel] Barcode registered:', barcode);

    // Update the receipt in messages to show registered barcode
    setMessages(prevMessages =>
      prevMessages.map(msg => {
        // Check if this message contains the product we just registered
        if (msg.content.includes(`data-product-id="${barcodeProductId}"`)) {
          // Replace the registration button with the registered barcode display
          const updatedContent = msg.content.replace(
            /<div data-product-id="[^"]*" data-product-name="[^"]*"[^>]*class="barcode-register-btn"[^>]*>❎ Daftarkan Barcode<\/div>/,
            `<div style="font-size:12px;color:#10b981;margin-top:4px">✅ Barcode: ${barcode}</div>`
          );
          return { ...msg, content: updatedContent };
        }
        return msg;
      })
    );

    // Close the modal
    setBarcodeModalOpen(false);
  };

  // Constants for inline autocomplete
  const KEYWORDS = ['pembelian', 'kulak', 'beli', 'belanja'];
  const WHOLESALE_UNITS = ['karton', 'dus', 'box', 'slop', 'bal', 'koli', 'pack', 'lusin'];
  const RETAIL_UNITS = ['pcs', 'unit', 'buah', 'biji', 'butir', 'lembar', 'batang', 'bungkus', 'sachet', 'botol'];
  const PAYMENT_METHODS = ['tunai', 'transfer', 'kredit', 'debit', 'qris'];

  // Enhanced autocomplete with real-time auto-correction
  const detectAutocompleteContext = (text: string) => {
    const lowerText = text.toLowerCase();

    // Check if starts with transaction keyword
    const hasKeyword = KEYWORDS.some(kw => lowerText.startsWith(kw));

    if (!hasKeyword) {
      setShowProductAutocomplete(false);
      setShowSupplierAutocomplete(false);
      setShowUnitDropdown(false);
      setShowPaymentDropdown(false);
      return;
    }

    // 1. Trigger product autocomplete after "Beli " (keyword + space)
    const keywordWithSpace = KEYWORDS.find(kw => lowerText === `${kw} `);
    if (keywordWithSpace && text !== lastInputRef.current) {
      setProductQuery('');
      setShowProductAutocomplete(true);
      setShowSupplierAutocomplete(false);
      setShowUnitDropdown(false);
      setShowPaymentDropdown(false);
      lastInputRef.current = text;
      return;
    }

    // Product autocomplete: after keyword, before "sejumlah"
    if (hasKeyword && !lowerText.includes('sejumlah')) {
      const keywordMatch = KEYWORDS.find(kw => lowerText.startsWith(kw));
      if (keywordMatch) {
        const afterKeyword = text.substring(keywordMatch.length).trim();
        if (afterKeyword.length >= 2) {
          setProductQuery(afterKeyword);
          setShowProductAutocomplete(true);
          setShowSupplierAutocomplete(false);
          setShowUnitDropdown(false);
          setShowPaymentDropdown(false);
          return;
        }
      }
    }

    // 2. Unit dropdown: after quantity number + space
    const quantityMatch = lowerText.match(/sejumlah\s+(\d+)\s+$/);
    if (quantityMatch && text !== lastInputRef.current) {
      setShowUnitDropdown(true);
      setShowProductAutocomplete(false);
      setShowSupplierAutocomplete(false);
      setShowPaymentDropdown(false);
      lastInputRef.current = text;
      return;
    }

    // 3. Auto-inject "harga " after manual unit typing
    const unitAfterQtyMatch = text.match(/sejumlah\s+\d+\s+(\w+)\s+$/i);
    if (unitAfterQtyMatch && !lowerText.includes('harga') && text !== lastInputRef.current) {
      const unit = unitAfterQtyMatch[1].toLowerCase();
      if ([...WHOLESALE_UNITS, ...RETAIL_UNITS].includes(unit)) {
        const newText = `${text.trim()} harga `;
        setMessage(newText);
        setDetectedUnit(unit);
        setShowUnitDropdown(false);
        lastInputRef.current = newText;
        return;
      }
    }

    // 4. Real-time rupiah auto-correction
    const rupiahMatch = text.match(/harga\s+(\d+(?:\.\d+)?)\s*(r|ri|rb|rib|ribu|k|jt|juta)?\s+$/i);
    if (rupiahMatch && text !== lastInputRef.current) {
      const number = parseFloat(rupiahMatch[1].replace(/\./g, ''));
      const suffix = rupiahMatch[2]?.toLowerCase() || '';

      let amount = number;
      if (suffix.includes('jt') || suffix.includes('juta')) {
        amount = number * 1_000_000;
      } else if (suffix.match(/^(r|ri|rb|rib|ribu|k)$/)) {
        amount = number * 1_000;
      }

      // Format with thousand separator
      const formatted = new Intl.NumberFormat('id-ID').format(amount);

      // Determine unit label
      const unitLabel = detectedUnit || 'unit';

      // Replace and inject template
      const beforeHarga = text.substring(0, text.toLowerCase().lastIndexOf('harga'));
      const newText = `${beforeHarga}harga Rp${formatted} per ${unitLabel} isi `;

      setMessage(newText);
      lastInputRef.current = newText;
      setShowUnitDropdown(false);
      setShowProductAutocomplete(false);
      return;
    }

    // 5. Auto-inject "per pcs dari " after isi quantity + space
    const isiMatch = text.match(/isi\s+(\d+)\s+$/i);
    if (isiMatch && !lowerText.includes('dari') && text !== lastInputRef.current) {
      const newText = `${text.trim()} per pcs dari `;
      setMessage(newText);
      lastInputRef.current = newText;
      setShowSupplierAutocomplete(true);
      setShowProductAutocomplete(false);
      setShowUnitDropdown(false);
      setShowPaymentDropdown(false);
      return;
    }

    // 6. Supplier autocomplete: after "dari " with query
    const dariMatch = lowerText.match(/dari\s+(.*)$/);
    if (dariMatch) {
      if (dariMatch[1].length === 0 && text !== lastInputRef.current) {
        // Just typed "dari " - show frequent suppliers
        setSupplierQuery('');
        setShowSupplierAutocomplete(true);
        setShowProductAutocomplete(false);
        setShowUnitDropdown(false);
        setShowPaymentDropdown(false);
        lastInputRef.current = text;
        return;
      } else if (dariMatch[1].length >= 2) {
        setSupplierQuery(dariMatch[1]);
        setShowSupplierAutocomplete(true);
        setShowProductAutocomplete(false);
        setShowUnitDropdown(false);
        setShowPaymentDropdown(false);
        return;
      }
    }

    // Hide all dropdowns if no context matches
    setShowProductAutocomplete(false);
    setShowSupplierAutocomplete(false);
    setShowUnitDropdown(false);
    setShowPaymentDropdown(false);
  };

  // Handle product selection from autocomplete
  const handleProductSelect = (productName: string) => {
    const lowerMessage = message.toLowerCase();
    const keyword = KEYWORDS.find(kw => lowerMessage.startsWith(kw)) || 'Pembelian';
    const newMessage = `${keyword.charAt(0).toUpperCase() + keyword.slice(1)} ${productName} sejumlah `;
    setMessage(newMessage);
    setShowProductAutocomplete(false);
    textareaRef.current?.focus();
  };

  // Handle unit selection
  const handleUnitSelect = (unit: string) => {
    const newMessage = `${message.trim()} ${unit} harga `;
    setMessage(newMessage);
    setDetectedUnit(unit);
    setShowUnitDropdown(false);
    lastInputRef.current = newMessage;
    textareaRef.current?.focus();
  };

  // Handle payment selection
  const handlePaymentSelect = (method: string) => {
    const newMessage = `${message.trim()} ${method} dari `;
    setMessage(newMessage);
    setShowPaymentDropdown(false);
    textareaRef.current?.focus();
  };

  // Handle supplier selection
  const handleSupplierSelect = (supplierName: string) => {
    const beforeDari = message.substring(0, message.toLowerCase().lastIndexOf('dari') + 4);
    const newMessage = `${beforeDari} ${supplierName}`;
    setMessage(newMessage);
    setShowSupplierAutocomplete(false);
    textareaRef.current?.focus();
  };

  // Handle transaction completion from form
  const handleTransactionComplete = (summary: string) => {
    console.log('[ChatPanel] Transaction completed, adding to chat:', summary);

    // Create assistant message with transaction summary
    const transactionMessage: Message = {
      id: `tx-${Date.now()}`,
      sender: 'assistant',
      content: summary,
      timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false })
    };

    // Add to messages
    setMessages(prev => [...prev, transactionMessage]);

    // Scroll to bottom after message added
    setTimeout(() => {
      scrollToBottom();
    }, 100);
  };

  // Handle external transaction message (from Desktop mode SalesTransaction)
  useEffect(() => {
    if (externalTransactionMessage) {
      handleTransactionComplete(externalTransactionMessage);
    }
  }, [externalTransactionMessage]);

  // Load chat history when tenant changes
  useEffect(() => {
    const loadChatHistory = async () => {
      console.log('[ChatPanel] 📋 loadChatHistory called, activeTenant:', activeTenant?.id);

      if (!activeTenant || activeTenant.id === 'milky-assistant') {
        // Milky Assistant doesn't have persistent history
        console.log('[ChatPanel] ⏭️ Skipping: no tenant or milky-assistant');
        setMessages([]);
        return;
      }

      const userId = getUserId();
      const isAuth = isAuthenticated();
      console.log('[ChatPanel] 🔑 Auth check:', { userId, isAuth, hasToken: !!localStorage.getItem('access_token') });

      if (!userId || !isAuth) {
        console.log('[ChatPanel] ⚠️ Not authenticated, skipping history load');
        setMessages([]);
        return;
      }

      setIsLoadingHistory(true);
      isLoadingOlderMessagesRef.current = false;  // Reset ref for clean scroll behavior on tenant switch
      console.log('[ChatPanel] 🔄 Loading history for:', { userId, tenantId: activeTenant.id });
      try {
        const history = await fetchChatHistory(userId, activeTenant.id, MESSAGES_PER_PAGE, 0);
        console.log('[ChatPanel] ✅ History response:', {
          messageCount: history?.messages?.length || 0,
          totalCount: history?.total_count,
          hasMore: history?.has_more,
          firstMsgId: history?.messages?.[0]?.id,
          lastMsgId: history?.messages?.[history?.messages?.length - 1]?.id
        });
        
        // Transform backend format (DESC order) to UI format (ASC order)
        const chatMessages: Message[] = [];
        
        // Reverse array: backend sends newest first, UI needs oldest first
        const sortedMessages = [...(history.messages || [])].reverse();
        
        sortedMessages.forEach((msg: any) => {
          // Parse timestamp safely
          let timestamp = 'Now';
          if (msg.created_at) {
            try {
              // Handle both number (Unix timestamp) and string formats
              const timestampValue = typeof msg.created_at === 'number' 
                ? msg.created_at 
                : parseInt(msg.created_at, 10);
              
              if (!isNaN(timestampValue) && timestampValue > 0) {
                // If timestamp is in seconds, convert to milliseconds
                const date = timestampValue < 10000000000 
                  ? new Date(timestampValue * 1000) 
                  : new Date(timestampValue);
                
                timestamp = date.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false });
              }
            } catch (e) {
              console.warn('[ChatPanel] Failed to parse timestamp:', msg.created_at, e);
            }
          }
          
          // Add user message
          chatMessages.push({
            id: `${msg.id}-user`,
            sender: 'user',
            content: msg.message || '',
            timestamp
          });
          
          // Add assistant response
          if (msg.response) {
            chatMessages.push({
              id: `${msg.id}-assistant`,
              sender: 'assistant',
              content: msg.response,
              timestamp
            });
          }
        });
        
        console.log('[ChatPanel] Transformed messages:', chatMessages.length);
        setMessages(chatMessages);
        // Set pagination state
        setHasMoreHistory(history.has_more || false);
        setHistoryOffset(MESSAGES_PER_PAGE);

        // Force scroll to bottom after history loads (backup for messages effect)
        setTimeout(() => {
          if (isMobile) {
            window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
          } else if (messagesContainerRef.current) {
            messagesContainerRef.current.scrollTo({
              top: messagesContainerRef.current.scrollHeight,
              behavior: 'auto'
            });
          }
        }, 150);
      } catch (error: any) {
        console.error('[ChatPanel] ❌ Failed to load chat history:', error);
        console.error('[ChatPanel] Error details:', error.message, error.stack);
        // Log auth state for debugging
        console.error('[ChatPanel] Auth state:', {
          hasToken: !!localStorage.getItem('access_token'),
          userId: getUserId(),
          isAuth: isAuthenticated()
        });
        // On error, start with empty messages (don't break UX)
        setMessages([]);
      } finally {
        setIsLoadingHistory(false);
      }
    };

    loadChatHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTenant?.id]);

  // Load more history (WhatsApp style - scroll up to load older messages)
  const loadMoreHistory = useCallback(async () => {
    if (isLoadingMore || !hasMoreHistory || !activeTenant) return;

    const userId = getUserId();
    if (!userId) return;

    // Save scroll position BEFORE loading (to restore after prepending messages)
    const previousScrollHeight = isMobile
      ? document.documentElement.scrollHeight
      : messagesContainerRef.current?.scrollHeight || 0;
    const previousScrollTop = isMobile
      ? window.scrollY
      : messagesContainerRef.current?.scrollTop || 0;

    setIsLoadingMore(true);
    isLoadingOlderMessagesRef.current = true;  // Prevent auto-scroll to bottom
    console.log('[ChatPanel] 📜 Loading more history, offset:', historyOffset);

    try {
      const history = await fetchChatHistory(userId, activeTenant.id, MESSAGES_PER_PAGE, historyOffset);
      console.log('[ChatPanel] 📜 Loaded more:', {
        count: history?.messages?.length || 0,
        hasMore: history?.has_more
      });

      // Transform older messages
      const olderMessages: Message[] = [];
      const sortedMessages = [...(history.messages || [])].reverse();

      sortedMessages.forEach((msg: any) => {
        let timestamp = 'Now';
        if (msg.created_at) {
          try {
            const timestampValue = typeof msg.created_at === 'number'
              ? msg.created_at
              : parseInt(msg.created_at, 10);

            if (!isNaN(timestampValue) && timestampValue > 0) {
              const date = timestampValue < 10000000000
                ? new Date(timestampValue * 1000)
                : new Date(timestampValue);
              timestamp = date.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false });
            }
          } catch (e) {
            console.warn('[ChatPanel] Failed to parse timestamp:', msg.created_at);
          }
        }

        olderMessages.push({
          id: `${msg.id}-user`,
          sender: 'user',
          content: msg.message || '',
          timestamp
        });

        if (msg.response) {
          olderMessages.push({
            id: `${msg.id}-assistant`,
            sender: 'assistant',
            content: msg.response,
            timestamp
          });
        }
      });

      // Prepend older messages to existing
      setMessages(prev => [...olderMessages, ...prev]);
      setHasMoreHistory(history.has_more || false);
      setHistoryOffset(prev => prev + MESSAGES_PER_PAGE);

      // Restore scroll position after DOM updates (maintain user's view position)
      setTimeout(() => {
        if (isMobile) {
          const newScrollHeight = document.documentElement.scrollHeight;
          const heightDiff = newScrollHeight - previousScrollHeight;
          window.scrollTo(0, previousScrollTop + heightDiff);
        } else if (messagesContainerRef.current) {
          const newScrollHeight = messagesContainerRef.current.scrollHeight;
          const heightDiff = newScrollHeight - previousScrollHeight;
          messagesContainerRef.current.scrollTop = previousScrollTop + heightDiff;
        }
        // Clear flag immediately after scroll position is restored
        isLoadingOlderMessagesRef.current = false;
      }, 50);
    } catch (error) {
      console.error('[ChatPanel] Failed to load more history:', error);
      isLoadingOlderMessagesRef.current = false;  // Clear ref on error to allow scroll
    } finally {
      setIsLoadingMore(false);
    }
  }, [activeTenant, historyOffset, hasMoreHistory, isLoadingMore, isMobile]);

  // Track if user is near bottom of chat
  const [showScrollButton, setShowScrollButton] = useState(false);
  // Track if user has scrolled down (for header border)
  const [isHeaderScrolled, setIsHeaderScrolled] = useState(false);

  // Auto-scroll to bottom - handles both mobile (body) and desktop (container)
  // smooth=false (default) for reliability on load, smooth=true for button tap animation
  const scrollToBottom = useCallback((smooth: boolean = false) => {
    // Use setTimeout to ensure DOM is updated
    setTimeout(() => {
      if (isMobile) {
        // Mobile: scroll body to bottom
        window.scrollTo({
          top: document.documentElement.scrollHeight,
          behavior: smooth ? 'smooth' : 'auto'
        });
      } else if (messagesContainerRef.current) {
        // Desktop: scroll container
        messagesContainerRef.current.scrollTo({
          top: messagesContainerRef.current.scrollHeight,
          behavior: smooth ? 'smooth' : 'auto'
        });
      }
      setShowScrollButton(false);
    }, 100);
  }, [isMobile]);

  // Check if user is near bottom (within 150px) - different source for mobile vs desktop
  // Also check if near top to load more history (WhatsApp style)
  const handleScroll = useCallback(() => {
    if (isMobile) {
      // Mobile: read from body/window
      const scrollTop = window.scrollY;
      const scrollHeight = document.documentElement.scrollHeight;
      const clientHeight = window.innerHeight;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      setShowScrollButton(distanceFromBottom > 150);
      // Header border when scrolled down from top
      setIsHeaderScrolled(scrollTop > 50);

      // Load more when near top (within 100px)
      if (scrollTop < 100 && hasMoreHistory && !isLoadingMore) {
        loadMoreHistory();
      }
    } else if (messagesContainerRef.current) {
      // Desktop: read from container
      const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      setShowScrollButton(distanceFromBottom > 150);
      // Header border when scrolled down from top
      setIsHeaderScrolled(scrollTop > 50);

      // Load more when near top (within 100px)
      if (scrollTop < 100 && hasMoreHistory && !isLoadingMore) {
        loadMoreHistory();
      }
    }
  }, [isMobile, hasMoreHistory, isLoadingMore, loadMoreHistory]);

  // Mobile: attach scroll listener to window
  useEffect(() => {
    if (!isMobile) return;

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, [isMobile, handleScroll]);

  useEffect(() => {
    // Skip auto-scroll when loading older messages (pagination)
    // Uses ref to avoid re-triggering effect when flag changes
    if (!isLoadingOlderMessagesRef.current) {
      scrollToBottom();
    }
  }, [messages, scrollToBottom]);

  // Auto-resize textarea and track height for border radius
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      const scrollHeight = textareaRef.current.scrollHeight;
      const maxHeight = 120; // Max 5 lines (24px per line)
      const newHeight = Math.min(scrollHeight, maxHeight);
      textareaRef.current.style.height = `${newHeight}px`;
      textareaRef.current.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';
      // Update height state for border radius (after height reset to 'auto')
      setTextareaHeight(scrollHeight);
    }
  }, [message]);

  const handleSend = async () => {
    if (!message.trim() || !activeTenant || isLoading) return;

    const messageText = message.trim();
    setMessage('');

    // Add user message immediately
    const userMessage: Message = {
      id: Date.now().toString(),
      sender: 'user',
      content: messageText,
      timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false }),
    };

    setMessages(prev => [...prev, userMessage]);

    // Show typing indicator
    setIsTyping(true);
    setIsLoading(true);

    try {
      // Real API call for all tenants (authenticated)
      if (isAuthenticated()) {
        const response = await sendTenantMessage(activeTenant.id, messageText);

        // Hide typing indicator
        setIsTyping(false);

        // Add response
        const botMessage: Message = {
          id: Date.now().toString(),
          sender: 'assistant',
          content: response.milky_response || response.message || 'Maaf, terjadi kesalahan.',
          timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false }),
        };
        setMessages(prev => [...prev, botMessage]);

        // ========== HYBRID CONVERSATIONAL POS: Handle action payload ==========
        if (response.action?.type === 'open_pos' && response.action?.payload) {
          const { items, paymentMethod } = response.action.payload;
          console.log('[ChatPanel] Detected sales intent action:', { items, paymentMethod });

          // Open POS panel with prefill data
          if (isDesktop && onPanelToggle) {
            // Desktop: Pass prefill data to Dashboard via callback, then open panel
            onPOSPrefillData?.({ items, paymentMethod });
            onPanelToggle('pos');
          } else {
            // Mobile: Use local state for full-screen modal
            setPOSPrefillData({ items, paymentMethod });
            setIsSalesOpen(true);
          }
        }
      } else {
        // Not authenticated - show error
        setIsTyping(false);
        const botMessage: Message = {
          id: Date.now().toString(),
          sender: 'assistant',
          content: 'Silakan login terlebih dahulu.',
          timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false }),
        };
        setMessages(prev => [...prev, botMessage]);
      }
    } catch (error) {
      console.error('Error sending message:', error);
      // Hide typing, show error
      setIsTyping(false);
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        sender: 'assistant',
        content: 'Maaf, terjadi kesalahan. Silakan coba lagi.',
        timestamp: 'Now'
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // On Mobile: Enter = new line (default behavior, use button to send)
    // On Desktop: Enter = send, Shift+Enter = new line
    const isMobile = isMobileDevice();

    if (!isMobile && e.key === 'Enter' && !e.shiftKey) {
      // Desktop: Enter without Shift = send
      e.preventDefault();
      handleSend();
    }
    // Mobile: Let Enter work as default (new line) - no preventDefault
  };

  if (!activeTenant) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-white text-gray-400">
        <svg className="w-16 h-16 mb-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <p className="text-sm">Select a tenant to start chatting</p>
      </div>
    );
  }

  // Main Chat View (split view is handled in Dashboard.tsx)
  return (
    <div
      className={`flex flex-col w-full ${isMobile ? 'min-h-screen' : 'h-full'}`}
      style={{
        minHeight: isMobile ? '100dvh' : undefined,
        height: !isMobile ? '100%' : undefined,
        width: '100%',
        overflow: isMobile ? 'visible' : 'hidden',
        position: !isMobile ? 'relative' : 'static',
        backgroundColor: '#F7F6F3', // Milkyhoop warm off-white
        isolation: 'isolate'
      }}
    >
      {/* HEADER - Mobile: fixed to viewport, Desktop: absolute to container */}
      <div
        className={`${isMobile ? 'fixed' : 'absolute'} top-0 left-0 right-0 z-50`}
        style={{
          paddingTop: isMobile ? 'env(safe-area-inset-top)' : '0',
          background: 'rgba(255, 255, 255, 0.85)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          borderBottom: isHeaderScrolled ? '1px solid #E8E6E1' : 'none',
          transition: 'border-bottom 0.2s ease',
        }}
      >
        {/* Header wrapper - padding: 12px 20px as per wireframe */}
        <div className="flex items-center py-3 px-5">
          {/* Zone 1: BACK (left side) - Mobile only, naked icon */}
          <button
            onClick={onBack}
            className="md:hidden flex items-center justify-center mr-3"
            aria-label="Back to workspace"
          >
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-gray-500"
            >
              <polyline points="15 19 9 12 15 5" />
            </svg>
          </button>

          {/* Zone 2: SETTINGS (avatar + name) */}
          <div
            onClick={() => onPanelToggle?.('settings')}
            className="flex items-center gap-3 flex-1 cursor-pointer"
          >
            {/* Tenant Avatar - coral/salmon color */}
            <div
              className="w-9 h-9 rounded-full flex items-center justify-center text-white font-semibold text-sm flex-shrink-0"
              style={{ backgroundColor: '#F87171' }}
            >
              {activeTenant.name.charAt(0).toUpperCase()}
            </div>

            {/* Tenant Name */}
            <span className="font-semibold text-gray-900" style={{ fontSize: '16px' }}>
              {activeTenant.name}
            </span>
          </div>
        </div>
      </div>

      {/* SCROLLABLE MESSAGES - Mobile: body scroll, Desktop: container scroll */}
      <div
        ref={messagesContainerRef}
        onScroll={!isMobile ? handleScroll : undefined}
        className={`flex-1 space-y-3 ${!isMobile ? 'min-h-0 overflow-y-auto' : ''}`}
        style={isMobile ? {
          // Mobile: natural flow, body scrolls for Safari toolbar auto-hide
          paddingTop: 'calc(76px + env(safe-area-inset-top))',
          paddingBottom: 'calc(120px + env(safe-area-inset-bottom))',
          paddingLeft: '16px',
          paddingRight: '16px',
          width: '100%',
          maxWidth: '100%',
          backgroundColor: 'transparent',
        } : {
          // Desktop: keep absolute scroll container
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: 0,
          right: 0,
          paddingTop: '76px',
          paddingBottom: '76px',
          paddingLeft: '16px',
          paddingRight: '16px',
          width: '100%',
          maxWidth: '100%',
          overflowX: 'hidden',
          overflowY: 'auto',
          backgroundColor: 'transparent',
          WebkitOverflowScrolling: 'touch',
          overscrollBehavior: 'contain'
        }}
      >
        {/* Loading Skeleton */}
        {isLoadingHistory && (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="flex gap-3 animate-pulse">
                <div className="w-8 h-8 rounded-full bg-gray-200 flex-shrink-0" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 bg-gray-200 rounded w-3/4" />
                  <div className="h-4 bg-gray-200 rounded w-1/2" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Empty State */}
        {!isLoadingHistory && messages.length === 0 && activeTenant && activeTenant.id !== 'milky-assistant' && (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 py-12">
            <svg className="w-16 h-16 mb-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <p className="text-sm text-gray-500">Belum ada pesan</p>
            <p className="text-xs text-gray-400 mt-1">Mulai percakapan dengan mengirim pesan</p>
          </div>
        )}

        {/* Loading more indicator - at top of messages */}
        {isLoadingMore && (
          <div className="flex justify-center py-3">
            <div className="flex items-center gap-2 text-gray-500 text-sm bg-white/80 px-4 py-2 rounded-full shadow-sm">
              <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
              </svg>
              Memuat pesan lama...
            </div>
          </div>
        )}

        {/* Messages */}
        {!isLoadingHistory && messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'} ${
              msg.sender === 'user' ? 'pr-2' : 'pl-2'
            }`}
          >
            {/* Check if this is a receipt/HTML content */}
            {(msg.content.includes('<table') || msg.content.includes('<div') || msg.content.includes('milky-receipt')) ? (
              // Receipt in balloon chat - bg-white
              <div className="max-w-[90%]">
                {/* Receipt balloon wrapper - bg-white, rounded */}
                <div className="bg-white rounded-2xl px-4 py-3" style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #d4d4d4' }}>
                  {/* Receipt content from backend */}
                  <div
                    dangerouslySetInnerHTML={{ __html: msg.content }}
                    className="receipt-card text-sm text-left"
                  />
                  {/* Timestamp inside balloon */}
                  <div className="text-xs text-neutral-400 mt-1 text-right">{msg.timestamp}</div>
                </div>

              </div>
            ) : (
              // Regular message bubble - Milkyhoop theme
              <div className="flex flex-col max-w-[80%]">
                <div
                  className="px-4 py-3"
                  style={msg.sender === 'user' ? {
                    // User bubble - warm beige with tail bottom-right
                    backgroundColor: '#E8E6E1',
                    color: '#1A1A1A',
                    borderRadius: '20px',
                    borderBottomRightRadius: '6px',
                  } : {
                    // Bot bubble - white with border, tail bottom-left
                    backgroundColor: '#FFFFFF',
                    color: '#1A1A1A',
                    border: '1px solid #E8E6E1',
                    borderRadius: '20px',
                    borderBottomLeftRadius: '6px',
                  }}
                >
                  <p className="text-[15px] leading-relaxed whitespace-pre-wrap break-words text-left">
                    {msg.content}
                  </p>
                </div>
                <span
                  className="text-xs mt-1 px-1"
                  style={{ color: '#9A9A9A', textAlign: msg.sender === 'user' ? 'right' : 'left' }}
                >
                  {msg.timestamp}
                </span>
              </div>
            )}
          </div>
        ))}
        
        {/* Typing Indicator - Milkyhoop Style */}
        {isTyping && (
          <div className="flex justify-start pl-2">
            <div
              className="px-4 py-3"
              style={{
                backgroundColor: '#FFFFFF',
                border: '1px solid #E8E6E1',
                borderRadius: '20px',
                borderBottomLeftRadius: '6px',
              }}
            >
              <TypingIndicator />
            </div>
          </div>
        )}
        
        {/* Invisible element to scroll to */}
        <div ref={messagesEndRef} style={{ height: '1px' }} />
      </div>

      {/* Scroll to Bottom Button - floating center, position: bottom 100px */}
      {showScrollButton && (
        <button
          onClick={() => scrollToBottom(true)}
          className={`${isMobile ? 'fixed' : 'absolute'} z-40 left-1/2 pointer-events-auto`}
          style={{
            transform: 'translateX(-50%)',
            bottom: isMobile ? 'calc(100px + env(safe-area-inset-bottom))' : '100px',
            width: '36px',
            height: '36px',
            borderRadius: '50%',
            background: 'rgba(255, 255, 255, 0.8)',
            backdropFilter: 'blur(8px)',
            WebkitBackdropFilter: 'blur(8px)',
            border: '1px solid rgba(0, 0, 0, 0.1)',
            boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            animation: 'fadeInUp 0.2s ease-out',
          }}
          aria-label="Scroll to bottom"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#9A9A9A"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M19 14l-7 7m0 0l-7-7m7 7V3" />
          </svg>
        </button>
      )}

      {/* INPUT AREA - Mobile: fixed to viewport, Desktop: absolute to container */}
      <div
        className={`${isMobile ? 'fixed' : 'absolute'} bottom-0 left-0 right-0 z-50`}
        style={{
          paddingBottom: isMobile ? 'env(safe-area-inset-bottom)' : '0px',
        }}
      >
        {/* Autocomplete dropdowns - positioned above input */}
        <div className="relative">
          {/* Product Autocomplete Dropdown (appears ABOVE) */}
          {showProductAutocomplete && (
            <div className="absolute bottom-full left-0 right-0 mb-2 max-h-60 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg z-50">
              <ProductAutocomplete
                value={productQuery}
                onChange={(value, suggestion) => {
                  if (suggestion) {
                    handleProductSelect(suggestion.name);
                  }
                }}
                placeholder=""
                disabled={false}
              />
            </div>
          )}

          {/* Unit Dropdown (appears ABOVE) */}
          {showUnitDropdown && (
            <div className="absolute bottom-full left-0 right-0 mb-2 max-h-48 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg z-50 p-2">
              <div className="text-xs text-gray-500 mb-2 px-2">Pilih Satuan:</div>
              <div className="text-xs font-semibold text-gray-600 px-2 py-1">Wholesales:</div>
              {WHOLESALE_UNITS.map(unit => (
                <button
                  key={unit}
                  onClick={() => handleUnitSelect(unit)}
                  className="w-full text-left px-3 py-2 hover:bg-purple-50 rounded text-sm"
                >
                  {unit}
                </button>
              ))}
              <div className="text-xs font-semibold text-gray-600 px-2 py-1 mt-2">Retail:</div>
              {RETAIL_UNITS.map(unit => (
                <button
                  key={unit}
                  onClick={() => handleUnitSelect(unit)}
                  className="w-full text-left px-3 py-2 hover:bg-purple-50 rounded text-sm"
                >
                  {unit}
                </button>
              ))}
            </div>
          )}

          {/* Payment Dropdown (appears ABOVE) */}
          {showPaymentDropdown && (
            <div className="absolute bottom-full left-0 right-0 mb-2 max-h-48 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg z-50 p-2">
              <div className="text-xs text-gray-500 mb-2 px-2">Metode Pembayaran:</div>
              {PAYMENT_METHODS.map(method => (
                <button
                  key={method}
                  onClick={() => handlePaymentSelect(method)}
                  className="w-full text-left px-3 py-2 hover:bg-purple-50 rounded text-sm capitalize"
                >
                  {method}
                </button>
              ))}
            </div>
          )}

          {/* Supplier Autocomplete Dropdown (appears ABOVE) */}
          {showSupplierAutocomplete && (
            <div className="absolute bottom-full left-0 right-0 mb-2 max-h-60 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg z-50">
              <SupplierAutocomplete
                value={supplierQuery}
                onChange={(value) => {
                  handleSupplierSelect(value);
                }}
                placeholder=""
                disabled={false}
              />
            </div>
          )}

          {/* Input Container with Fade Gradient */}
          <div className="relative">
            {/* Fade gradient - transparent to #F7F6F3 */}
            <div
              className="absolute top-0 left-0 right-0 pointer-events-none"
              style={{
                height: '80px',
                background: 'linear-gradient(to bottom, transparent 0%, #F7F6F3 70%)',
                transform: 'translateY(-100%)',
              }}
            />

            {/* Input pill container */}
            <div className="px-0 py-5 flex justify-center">
              {/* Main pill - Milkyhoop style */}
              <div
                className="relative flex items-end gap-2 py-2 px-2 transition-all duration-200"
                style={{
                  backgroundColor: '#F7F6F3',
                  borderRadius: textareaHeight > 44 ? '24px' : '100px',
                  boxShadow: '0 4px 24px rgba(0, 0, 0, 0.12)',
                }}
              >
                {/* [+] Button - circle, stays at bottom */}
                <button
                  className="flex-shrink-0 self-end flex items-center justify-center cursor-pointer transition-colors"
                  style={{
                    width: '48px',
                    height: '48px',
                    borderRadius: '50%',
                    background: 'none',
                  }}
                >
                  <ActionMenuButton
                    isMobile={isMobile}
                    onMenuItemClick={handleActionMenuClick}
                    isOpen={isActionMenuOpen}
                    onOpenChange={setIsActionMenuOpen}
                  />
                </button>

                {/* Middle section with textarea */}
                <div className="flex-1 flex items-center min-h-[48px] px-3">
                  <textarea
                    ref={textareaRef}
                    value={message}
                    onChange={(e) => {
                      setMessage(e.target.value);
                      detectAutocompleteContext(e.target.value);
                    }}
                    onKeyPress={handleKeyPress}
                    placeholder="Ketik pesan..."
                    rows={1}
                    className="flex-1 bg-transparent border-none outline-none focus:ring-0 focus:outline-none resize-none"
                    style={{
                      minHeight: '24px',
                      maxHeight: '120px',
                      minWidth: '120px',
                      border: 'none',
                      outline: 'none',
                      boxShadow: 'none',
                      lineHeight: '1.4',
                      fontSize: '15px',
                      color: '#1A1A1A',
                    }}
                  />
                </div>

                {/* Send / Speak Button - stays at bottom */}
                {message.trim() ? (
                  // Send button when message exists
                  <button
                    onClick={handleSend}
                    disabled={isLoading}
                    className="flex-shrink-0 flex items-center justify-center disabled:opacity-50 transition-all self-end"
                    style={{ width: '48px', height: '48px' }}
                    aria-label="Send message"
                  >
                    <img
                      src="/icons/sendbutton.png"
                      alt="Send"
                      style={{ width: '32px', height: '32px', objectFit: 'contain' }}
                    />
                  </button>
                ) : (
                  // Speak button - dark gray circle
                  <button
                    onClick={() => console.log('[ChatPanel] Speak button clicked - placeholder')}
                    className="flex-shrink-0 flex items-center justify-center transition-all self-end"
                    style={{
                      width: '48px',
                      height: '48px',
                      borderRadius: '50%',
                      backgroundColor: '#4A4A4A',
                    }}
                    aria-label="Speak"
                  >
                    <svg
                      width="24"
                      height="24"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="#FFFFFF"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* CSS for animations and receipt styling */}
        <style>{`
          @keyframes fadeInScale {
            from {
              opacity: 0;
              transform: scale(0.8);
            }
            to {
              opacity: 1;
              transform: scale(1);
            }
          }

          @keyframes fadeInUp {
            from {
              opacity: 0;
              transform: translateY(8px);
            }
            to {
              opacity: 1;
              transform: translateY(0);
            }
          }

          @keyframes typing {
            0%, 60%, 100% {
              transform: translateY(0);
              opacity: 0.4;
            }
            30% {
              transform: translateY(-4px);
              opacity: 1;
            }
          }

          /* Receipt card styling - labels left, values right */
          .receipt-card table {
            width: 100%;
            text-align: left;
          }
          .receipt-card td:first-child {
            text-align: left;
            color: #666;
          }
          .receipt-card td:last-child {
            text-align: right;
            font-weight: 500;
          }
          .receipt-card tr {
            border-bottom: 1px solid #e5e7eb;
          }
          .receipt-card tr td {
            padding: 6px 0;
          }
          /* GRAND TOTAL with lime underline */
          .receipt-card div[style*="GRAND TOTAL"],
          .receipt-card span:contains("GRAND TOTAL"),
          .receipt-card td:contains("GRAND TOTAL") {
            border-bottom: 2px solid #65a30d;
            padding-bottom: 8px;
          }
          /* Force milky-receipt to be transparent with no padding */
          .milky-receipt {
            background: transparent !important;
            padding: 0 !important;
            border-radius: 0 !important;
          }
          /* Compact receipt header */
          .milky-receipt > div:first-child {
            margin-bottom: 12px !important;
            padding-bottom: 12px !important;
          }
        `}</style>
      </div>

      {/* Purchase Form - Only for mobile (desktop uses split view from Dashboard) */}
      <AnimatePresence>
        {isPurchaseFormOpen && !isDesktop && (
          <motion.div
            key="purchase-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50"
          >
            <PurchaseForm
              isOpen={true}
              onClose={() => {
                setIsPurchaseFormOpen(false);
                setScannedProductData(null); // Clear scanned data on close
              }}
              isMobile={isMobile}
              isEmbedded={true}
              onTransactionComplete={handleTransactionComplete}
              scannedProductData={scannedProductData}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Beban (Expense) - Only for mobile (desktop uses split view from Dashboard) */}
      <AnimatePresence>
        {isExpenseFormOpen && !isDesktop && (
          <motion.div
            key="beban-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50"
          >
            <Beban
              tenantId={activeTenant?.id || ''}
              onClose={() => setIsExpenseFormOpen(false)}
              onTransactionComplete={handleTransactionComplete}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Purchase Invoice List Panel (Faktur Pembelian) - Only for mobile */}
      <AnimatePresence>
        {isPurchaseInvoiceListOpen && !isDesktop && (
          <PurchaseInvoicePanel
            isOpen={isPurchaseInvoiceListOpen}
            onClose={() => setIsPurchaseInvoiceListOpen(false)}
            isMobile={isMobile}
            onCreateNew={() => {
              setIsPurchaseInvoiceFormOpen(true);
            }}
          />
        )}
      </AnimatePresence>

      {/* Purchase Invoice Form (Buat Faktur) - from list panel FAB */}
      <AnimatePresence>
        {isPurchaseInvoiceFormOpen && !isDesktop && (
          <motion.div
            key="purchase-invoice-form-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-[60]"
          >
            <PurchaseInvoiceForm
              isOpen={isPurchaseInvoiceFormOpen}
              onClose={() => setIsPurchaseInvoiceFormOpen(false)}
              isMobile={isMobile}
              onTransactionComplete={(summary) => {
                handleTransactionComplete(summary);
                setIsPurchaseInvoiceFormOpen(false);
                // Optionally close list panel too, or refresh it
              }}
              showCreateHeader={true}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Purchase Invoice Form (Legacy - direct access) - Only for mobile */}
      {isPurchaseInvoiceOpen && !isDesktop && (
        <PurchaseInvoiceForm
          isOpen={isPurchaseInvoiceOpen}
          onClose={() => setIsPurchaseInvoiceOpen(false)}
          isMobile={isMobile}
          onTransactionComplete={handleTransactionComplete}
        />
      )}

      {/* Inventory Panel - Only for mobile (desktop uses split view from Dashboard) */}
      {!isDesktop && (
        <InventoryPanel
          isOpen={isInventoryOpen}
          onClose={() => setIsInventoryOpen(false)}
          isMobile={isMobile}
        />
      )}

      {/* Kas & Bank Panel - Only for mobile (desktop uses split view from Dashboard) */}
      <AnimatePresence>
        {isKasBankOpen && !isDesktop && (
          <motion.div
            key="kasbank-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50"
          >
            <KasBankPanel
              isOpen={isKasBankOpen}
              onClose={() => setIsKasBankOpen(false)}
              isMobile={isMobile}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Hutang/Piutang Panel - Only for mobile (desktop uses split view from Dashboard) */}
      <AnimatePresence>
        {isDebtOpen && !isDesktop && (
          <motion.div
            key="debt-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50"
          >
            <DebtPanel
              isOpen={isDebtOpen}
              onClose={() => setIsDebtOpen(false)}
              isMobile={isMobile}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Pelanggan Panel - Only for mobile (desktop uses split view from Dashboard) */}
      <AnimatePresence>
        {isCustomerOpen && !isDesktop && (
          <motion.div
            key="customer-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50"
          >
            <CustomerPanel
              isOpen={isCustomerOpen}
              onClose={() => setIsCustomerOpen(false)}
              isMobile={isMobile}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Insight Panel - Only for mobile (desktop uses split view from Dashboard) */}
      <AnimatePresence>
        {isInsightOpen && !isDesktop && (
          <motion.div
            key="insight-modal"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50"
          >
            <InsightPanel
              isOpen={isInsightOpen}
              onClose={() => setIsInsightOpen(false)}
              isMobile={isMobile}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Barcode Registration Modal */}
      <BarcodeRegistrationModal
        isOpen={barcodeModalOpen}
        productId={barcodeProductId}
        productName={barcodeProductName}
        onClose={() => setBarcodeModalOpen(false)}
        onSuccess={handleBarcodeSuccess}
      />

      {/* Sales Transaction Modal - Only for mobile (desktop uses split view) */}
      <AnimatePresence>
        {isSalesOpen && activeTenant && !isDesktop && (
          <motion.div
            key="pos-mobile"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50 bg-white"
          >
            <SalesTransaction
              tenantId={activeTenant.id}
              onClose={() => {
                setIsSalesOpen(false);
                setPOSPrefillData(null); // Clear prefill data when closing
              }}
              onTransactionComplete={handleTransactionComplete}
              // HYBRID CONVERSATIONAL POS: Pass prefill data
              initialItems={posPrefillData?.items}
              initialPaymentMethod={posPrefillData?.paymentMethod as 'tunai' | 'qris' | 'hutang' | undefined}
              onPrefillProcessed={() => setPOSPrefillData(null)}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Pembelian Modal - Mobile full-screen */}
      <AnimatePresence>
        {isPembelianOpen && activeTenant && (
          <motion.div
            key="pembelian-mobile"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={slideTransition}
            className="fixed inset-0 z-50 bg-white"
          >
            <Pembelian
              tenantId={activeTenant.id}
              onClose={() => setIsPembelianOpen(false)}
              onTransactionComplete={handleTransactionComplete}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Dashboard Panel - always mounted for smooth gesture, positioned off-screen when closed */}
      {isMobile && !isDesktop && (
        <DashboardPanel
          isOpen={isDashboardOpen}
          onClose={() => setIsDashboardOpen(false)}
          onOpen={() => setIsDashboardOpen(true)}
          onActionClick={handleDashboardActionClick}
          onSummaryCardClick={handleSummaryCardClick}
          isMobile={true}
        />
      )}
    </div>
  );
};

export default ChatPanel;
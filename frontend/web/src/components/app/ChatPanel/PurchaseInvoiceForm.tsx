/**
 * PurchaseInvoiceForm - Faktur Pembelian (Bill)
 * Multi-item purchase form - creates AP record
 * Tiimo-style UI: Flat list with accordion pattern
 * Refactored from PurchaseInvoiceForm for proper accrual accounting
 */
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Fuse from 'fuse.js';
import {
  PurchaseInvoiceFormData,
  PurchaseInvoiceFormProps,
  PurchaseInvoiceFormErrors,
  PurchaseItem,
  ItemErrors,
  WAREHOUSE_OPTIONS,
  UNIT_OPTIONS,
  getInitialFormData,
  getDefaultDueDate,
  createEmptyItem,
  calculateItemSubtotal,
  formatCurrency,
  parseCurrency,
} from '../../../types/purchaseInvoice';

const DRAFT_KEY = 'milkyhoop_draft_buy_pay_full';

// Colors - Round 7: Reverted to Tailwind 50
const COLORS = {
  // Required field (rose-50)
  requiredBg: '#FFF1F2',  // rose-50
  // Optional field (sky-50)
  optionalBg: '#F0F9FF',  // sky-50
  // Calculated field (soft gray)
  calcBg: '#FAFAFA',      // gray-50
  // Text - consistent neutral-800
  neutral800: '#262626',
  // General
  white: '#FFFFFF',
  gray50: '#FAFAFA',
  gray100: '#F5F5F5',
  gray200: '#E5E5E5',
  gray300: '#D4D4D4',
  gray400: '#A3A3A3',
  gray500: '#737373',
  gray600: '#525252',
  gray700: '#404040',
  gray800: '#262626',
  green: '#22C55E',
  greenLight: '#DCFCE7',
  red: '#EF4444',
  redLight: '#FEE2E2',
};

// Icon props type
type IconProps = { className?: string; style?: React.CSSProperties };

// Field Icons - all accept className and style
const Icons = {
  Building: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
    </svg>
  ),
  Calendar: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
  ),
  Wallet: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 12a2.25 2.25 0 00-2.25-2.25H15a3 3 0 11-6 0H5.25A2.25 2.25 0 003 12m18 0v6a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 18v-6m18 0V9M3 12V9m18 0a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 9m18 0V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v3" />
    </svg>
  ),
  Warehouse: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
    </svg>
  ),
  ShoppingCart: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z" />
    </svg>
  ),
  Calculator: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15.75 15.75V18m-7.5-6.75h.008v.008H8.25v-.008zm0 2.25h.008v.008H8.25V13.5zm0 2.25h.008v.008H8.25v-.008zm0 2.25h.008v.008H8.25V18zm2.498-6.75h.007v.008h-.007v-.008zm0 2.25h.007v.008h-.007V13.5zm0 2.25h.007v.008h-.007v-.008zm0 2.25h.007v.008h-.007V18zm2.504-6.75h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V13.5zm0 2.25h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V18zm2.498-6.75h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V13.5zM8.25 6h7.5v2.25h-7.5V6zM12 2.25c-1.892 0-3.758.11-5.593.322C5.307 2.7 4.5 3.65 4.5 4.757V19.5a2.25 2.25 0 002.25 2.25h10.5a2.25 2.25 0 002.25-2.25V4.757c0-1.108-.806-2.057-1.907-2.185A48.507 48.507 0 0012 2.25z" />
    </svg>
  ),
  Document: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
  ),
  Tag: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.568 3H5.25A2.25 2.25 0 003 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 005.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 009.568 3z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6 6h.008v.008H6V6z" />
    </svg>
  ),
  Percent: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6.75 6.75h.008v.008H6.75V6.75zm0 10.5h.008v.008H6.75v-.008zm10.5-10.5h.008v.008h-.008V6.75zm0 10.5h.008v.008h-.008v-.008zM6.75 17.25L17.25 6.75" />
    </svg>
  ),
  Receipt: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 14.25l6-6m4.5-3.493V21.75l-3.75-1.5-3.75 1.5-3.75-1.5-3.75 1.5V4.757c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0c1.1.128 1.907 1.077 1.907 2.185zM9.75 9h.008v.008H9.75V9zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm4.125 4.5h.008v.008h-.008V13.5zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
    </svg>
  ),
  PencilSquare: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
    </svg>
  ),
  Paperclip: ({ className = '', style }: IconProps) => (
    <svg className={className} style={style} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13" />
    </svg>
  ),
};

// Chevron Icon - Round 10: Points UP when collapsed with value
interface ChevronIconProps {
  isExpanded: boolean;
  hasValue?: boolean;  // Round 10: chevron points UP when collapsed with value
  className?: string;
}

const ChevronIcon: React.FC<ChevronIconProps> = ({ isExpanded, hasValue = false, className = '' }) => {
  // Round 21: Simplified - only rotate when expanded
  // Using inline styles for reliable animation across all browsers
  const shouldRotate = isExpanded;

  return (
    <svg
      className={`w-4 h-4 ${className}`}
      style={{
        transform: shouldRotate ? 'rotate(180deg)' : 'rotate(0deg)',
        transition: 'transform 0.2s ease',
        color: COLORS.gray500,
      }}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
    </svg>
  );
};

// ActionIcon - Round 10: 4 states for expand/collapse/check/clear
// State 1: Collapsed WITHOUT value → + (plus)
// State 2: Collapsed WITH value → ✓ (check) - indicates filled
// State 3: Expanded WITHOUT value → - (minus)
// State 4: Expanded WITH value → × (gray, not red)
interface ActionIconProps {
  isExpanded: boolean;
  hasValue?: boolean;
  onClick: () => void;
  onClear?: () => void;
  className?: string;
}

const ActionIcon: React.FC<ActionIconProps> = ({ isExpanded, hasValue = false, onClick, onClear, className = '' }) => {
  const handleClick = () => {
    if (isExpanded && hasValue && onClear) {
      onClear();
    } else {
      onClick();
    }
  };

  // All states use gray bg and gray stroke - no red
  const bgColor = COLORS.gray100;
  const strokeColor = COLORS.gray500;

  // Determine which icon to show based on 4 states
  const renderIcon = () => {
    if (!isExpanded && hasValue) {
      // State 2: Collapsed WITH value → Check icon (indicates filled)
      return (
        <svg className="w-4 h-4" fill="none" stroke={strokeColor} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      );
    } else if (!isExpanded && !hasValue) {
      // State 1: Collapsed WITHOUT value → Plus icon
      return (
        <svg className="w-4 h-4" fill="none" stroke={strokeColor} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
      );
    } else if (isExpanded && hasValue) {
      // State 4: Expanded WITH value → X icon (gray, for clearing)
      return (
        <svg className="w-4 h-4" fill="none" stroke={strokeColor} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    } else {
      // State 3: Expanded WITHOUT value → Minus icon
      return (
        <svg className="w-4 h-4" fill="none" stroke={strokeColor} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
        </svg>
      );
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`w-8 h-8 flex items-center justify-center rounded-full transition-colors flex-shrink-0 ${className}`}
      style={{ backgroundColor: bgColor }}
    >
      {renderIcon()}
    </button>
  );
};

// Filled Value Pill - Round 12: Solid border (no shadow), left-aligned text
interface FilledValuePillProps {
  value: string;
}

const FilledValuePill: React.FC<FilledValuePillProps> = ({ value }) => {
  return (
    <div
      className="px-4 py-2.5 rounded-lg"
      style={{
        backgroundColor: COLORS.white,
        // Round 21: Reduced border from 2px to 1px for thinner appearance
        border: `1px solid ${COLORS.gray300}`,
      }}
    >
      {/* Round 10: Left-align text instead of center */}
      <span className="text-sm text-left block" style={{ color: COLORS.neutral800 }}>{value}</span>
    </div>
  );
};

// FieldInputBar Component - Round 13: Keyboard trigger fix, scroll container
interface FieldInputBarProps {
  fieldIcon: React.FC<IconProps>;  // Round 10: field icon component instead of text
  fieldType: 'required' | 'optional';
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onClose: () => void;
  suggestions?: Array<{ name: string }>;
  onSelectSuggestion?: (value: string) => void;
  inputType?: 'text' | 'date' | 'number';
  targetFieldRef?: React.RefObject<HTMLDivElement>;  // Round 12: ref to field being edited
  scrollContainerRef?: React.RefObject<HTMLDivElement>;  // Round 13: ref to scrollable container
}

const FieldInputBar: React.FC<FieldInputBarProps> = ({
  fieldIcon: FieldIcon,
  fieldType,
  value,
  onChange,
  onSubmit,
  onClose,
  suggestions = [],
  onSelectSuggestion,
  inputType = 'text',
  targetFieldRef,
  scrollContainerRef,  // Round 13: scrollable container ref
}) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  // Round 12: Track previous keyboard height to detect dismiss
  const prevKeyboardHeightRef = useRef(0);
  // Round 12 fix: Only start detecting dismiss after keyboard is stable
  const isKeyboardReadyRef = useRef(false);
  const keyboardReadyTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Round 12: Track keyboard height using visualViewport API + dismiss detection
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;

    // Reset keyboard ready state
    isKeyboardReadyRef.current = false;

    // Wait 800ms for keyboard to fully appear before enabling dismiss detection
    keyboardReadyTimeoutRef.current = setTimeout(() => {
      isKeyboardReadyRef.current = true;
    }, 800);

    const handleResize = () => {
      // Calculate keyboard height from viewport difference
      const windowHeight = window.innerHeight;
      const viewportHeight = viewport.height;
      const newKeyboardHeight = Math.max(0, windowHeight - viewportHeight - viewport.offsetTop);

      // Round 12: Detect keyboard dismiss (was open, now closed)
      // Only detect dismiss after keyboard is ready (to avoid false triggers during open animation)
      if (isKeyboardReadyRef.current && prevKeyboardHeightRef.current > 100 && newKeyboardHeight < 50) {
        // Keyboard was dismissed - close FieldInputBar
        onClose();
      }

      prevKeyboardHeightRef.current = newKeyboardHeight;
      setKeyboardHeight(newKeyboardHeight);
    };

    // Initial check
    handleResize();

    viewport.addEventListener('resize', handleResize);
    viewport.addEventListener('scroll', handleResize);

    return () => {
      viewport.removeEventListener('resize', handleResize);
      viewport.removeEventListener('scroll', handleResize);
      if (keyboardReadyTimeoutRef.current) {
        clearTimeout(keyboardReadyTimeoutRef.current);
      }
    };
  }, [onClose]);

  // Round 14: Sync scroll container height with visualViewport (keyboard-aware)
  useEffect(() => {
    if (!scrollContainerRef?.current) return;
    const sc = scrollContainerRef.current;

    const updateHeight = () => {
      const h = window.visualViewport
        ? window.visualViewport.height
        : window.innerHeight;
      // Round 18: Header is ~44px (py-1 + content), use 48px with buffer
      // Don't subtract FieldInputBar height since it's position:fixed
      const headerHeight = 48;
      sc.style.height = `${h - headerHeight}px`;
    };

    updateHeight();
    window.visualViewport?.addEventListener('resize', updateHeight);
    window.addEventListener('resize', updateHeight);

    return () => {
      window.visualViewport?.removeEventListener('resize', updateHeight);
      window.removeEventListener('resize', updateHeight);
      sc.style.height = ''; // Reset on unmount
    };
  }, [scrollContainerRef]);

  // Round 20: Transfer focus from hidden input to real input with preventScroll
  useEffect(() => {
    if (!inputRef.current) return;

    // Small delay to allow animation, then transfer focus
    const timer = setTimeout(() => {
      inputRef.current?.focus({ preventScroll: true });
    }, 100);

    return () => clearTimeout(timer);
  }, []);

  const bgColor = fieldType === 'required' ? COLORS.requiredBg : COLORS.optionalBg;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && value.trim()) {
      onSubmit();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  // Round 10: Handle blur to sync with keyboard dismiss
  const handleInputBlur = () => {
    // Delay to allow for suggestion clicks
    setTimeout(() => {
      if (!containerRef.current?.contains(document.activeElement)) {
        onClose();
      }
    }, 200);
  };

  return (
    <div
      ref={containerRef}
      className="fixed left-0 right-0 z-50 bg-white field-input-bar"
      style={{
        // Round 11: Position above keyboard
        bottom: keyboardHeight > 0 ? keyboardHeight : 0,
        paddingBottom: keyboardHeight > 0 ? 0 : 'env(safe-area-inset-bottom)',
        transition: 'bottom 0.1s ease-out',
      }}
    >
      {/* Round 21: Autocomplete suggestions with improved styling */}
      {suggestions.length > 0 && (
        <div className="px-4 pb-2">
          <div
            className="rounded-xl max-h-48 overflow-y-auto"
            style={{
              backgroundColor: COLORS.white,
              border: `1px solid ${COLORS.gray200}`,
              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.08)',
            }}
          >
            {suggestions.map((s, i) => (
              <button
                key={i}
                className="w-full px-4 py-3 text-left text-sm hover:bg-gray-50 first:rounded-t-xl last:rounded-b-xl transition-colors"
                style={{ color: COLORS.neutral800 }}
                onMouseDown={() => onSelectSuggestion?.(s.name)}
              >
                <div className="flex items-center gap-2">
                  <svg className="w-4 h-4 flex-shrink-0" style={{ color: COLORS.gray400 }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                  <span className="font-medium">{s.name}</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input bar */}
      <div className="px-4 py-3">
        <div
          className="flex items-center gap-2 bg-white pl-2 pr-3 py-2 rounded-full"
          style={{ border: '1px solid #e5e5e5' }}
        >
          {/* Round 10: Field icon circle (LEFT) - replaces text pill */}
          <div
            className="flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-full"
            style={{ backgroundColor: bgColor }}
          >
            <FieldIcon className="w-5 h-5" style={{ color: COLORS.neutral800 }} />
          </div>

          {/* Text input (CENTER) - Round 20: NO autoFocus, focus transferred from hidden input */}
          <input
            ref={inputRef}
            type={inputType}
            inputMode="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleInputBlur}
            placeholder=""
            className="flex-1 bg-transparent border-none outline-none text-sm py-2"
            style={{
              color: COLORS.neutral800,
              minHeight: '20px',
            }}
          />

          {/* Speak or Send button (RIGHT) */}
          {value.trim() ? (
            <button
              onClick={onSubmit}
              className="flex-shrink-0 flex items-center justify-center"
              style={{ width: '29px', height: '29px' }}
            >
              <img src="/icons/sendbutton.png" alt="Send" style={{ width: '29px', height: '29px', objectFit: 'contain' }} />
            </button>
          ) : (
            <button
              onClick={onClose}
              className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full"
              style={{ backgroundColor: '#262626' }}
            >
              <svg className="w-4 h-4" style={{ color: '#ffffff' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
              <span className="text-white text-sm font-medium">Speak</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

// Label Chip Component - Round 10: Added hasValue for chevron direction
interface LabelChipProps {
  type: 'required' | 'optional' | 'calculated';
  label: string;
  icon: React.FC<IconProps>;
  isExpanded?: boolean;
  hasValue?: boolean;  // Round 10: pass to ChevronIcon for UP direction when filled
  onToggle?: () => void;
}

const LabelChip: React.FC<LabelChipProps> = ({ type, label, icon: Icon, isExpanded = false, hasValue = false, onToggle }) => {
  const bgColors = {
    required: COLORS.requiredBg,
    optional: COLORS.optionalBg,
    calculated: COLORS.calcBg,
  };

  // Format label: uppercase and replace "atau" with "/"
  const formatLabel = (text: string) => {
    return text
      .replace(' atau ', ' / ')
      .toUpperCase();
  };

  const content = (
    <>
      <Icon className="w-4 h-4" style={{ color: COLORS.neutral800 }} />
      <span className="text-sm font-semibold tracking-wide" style={{ color: COLORS.neutral800 }}>
        {formatLabel(label)}
      </span>
      {onToggle && (
        <ChevronIcon isExpanded={isExpanded} hasValue={hasValue} className="ml-1" />
      )}
    </>
  );

  if (onToggle) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-full transition-colors hover:opacity-90"
        style={{ backgroundColor: bgColors[type] }}
      >
        {content}
      </button>
    );
  }

  return (
    <div
      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-full"
      style={{ backgroundColor: bgColors[type] }}
    >
      {content}
    </div>
  );
};

// Supplier suggestion interface
interface SupplierSuggestion {
  name: string;
  usage_count: number;
}

// Product suggestion interface
interface ProductSuggestion {
  name: string;
  unit: string;
  last_price: number | null;
  usage_count: number;
  harga_jual: number | null;
  units_per_pack: number | null;
}

const PurchaseInvoiceForm: React.FC<PurchaseInvoiceFormProps> = ({
  isOpen,
  onClose,
  isMobile = false,
  isEmbedded = false,
  onTransactionComplete,
  showCreateHeader = false,
}) => {
  // Form state
  const [formData, setFormData] = useState<PurchaseInvoiceFormData>(getInitialFormData());
  const [errors, setErrors] = useState<PurchaseInvoiceFormErrors>({});
  const [itemErrors, setItemErrors] = useState<Record<string, ItemErrors>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStatus, setSubmitStatus] = useState<'idle' | 'success' | 'error'>('idle');

  // UI state - Track expanded state for each field
  // Round 7: ALL fields collapsed by default
  const [expandedFields, setExpandedFields] = useState<Record<string, boolean>>({
    // Required fields - NOW collapsed by default
    supplier: false,
    transactionDate: false,
    dueDate: false,        // Jatuh Tempo (replaced fundSource)
    warehouse: false,
    items: false,
    // Price preference fields
    discountType: false,   // Tipe diskon (expandable checkboxes)
    // Optional fields - collapsed by default
    receiveDate: false,
    invoiceNumber: false,
    referenceNumber: false,
    invoiceDiscount: false,
    cashDiscount: false,
    taxBase: false,
    tax: false,
    notes: false,
    attachments: false,
  });

  const [showDraftPrompt, setShowDraftPrompt] = useState(false);

  // Autocomplete state - Round 22: Prefetch + Fuse.js for INSTANT filtering
  const [showSupplierDropdown, setShowSupplierDropdown] = useState(false);
  const [supplierSearch, setSupplierSearch] = useState('');
  const [supplierSuggestions, setSupplierSuggestions] = useState<SupplierSuggestion[]>([]);
  const [isLoadingSuppliers, setIsLoadingSuppliers] = useState(false);

  // Round 22: Prefetch state for instant autocomplete
  const [allSuppliers, setAllSuppliers] = useState<SupplierSuggestion[]>([]);
  const [supplierFuse, setSupplierFuse] = useState<Fuse<SupplierSuggestion> | null>(null);
  const [suppliersReady, setSuppliersReady] = useState(false);

  // Product autocomplete per item
  const [productSearches, setProductSearches] = useState<Record<string, string>>({});
  const [productSuggestions, setProductSuggestions] = useState<Record<string, ProductSuggestion[]>>({});
  const [showProductDropdowns, setShowProductDropdowns] = useState<Record<string, boolean>>({});
  const [isLoadingProducts, setIsLoadingProducts] = useState<Record<string, boolean>>({});

  // Expanded items
  const [expandedItemIds, setExpandedItemIds] = useState<Set<string>>(new Set());

  // Round 9: FieldInputBar state - tracks which field is using bottom input bar
  const [activeInputField, setActiveInputField] = useState<string | null>(null);
  const [inputBarValue, setInputBarValue] = useState('');

  const modalRef = useRef<HTMLDivElement>(null);
  // Round 22: Removed supplierDebounceRef - no longer needed with Fuse.js instant search
  const productDebounceRefs = useRef<Record<string, NodeJS.Timeout | null>>({});

  // Round 8: Auto-focus refs
  const supplierInputRef = useRef<HTMLInputElement>(null);
  // Round 12: Field container refs for scroll into view
  const supplierFieldRef = useRef<HTMLDivElement>(null);
  // Round 13: Scrollable container ref
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  // Round 20: Hidden input ref for Safari keyboard trigger (must be in DOM before tap)
  const hiddenInputRef = useRef<HTMLInputElement>(null);
  // Round 21: Date input ref for triggering native date picker
  const dateInputRef = useRef<HTMLInputElement>(null);
  // Due Date input ref for Jatuh Tempo
  const dueDateInputRef = useRef<HTMLInputElement>(null);

  // Round 8: Auto-focus on expand
  useEffect(() => {
    if (expandedFields.supplier && supplierInputRef.current && !formData.supplier) {
      // Small delay to wait for animation
      setTimeout(() => {
        supplierInputRef.current?.focus();
      }, 100);
    }
  }, [expandedFields.supplier, formData.supplier]);

  const toggleField = (field: string) => {
    setExpandedFields(prev => ({ ...prev, [field]: !prev[field] }));
  };

  // Round 9: Clear field value
  const clearField = (field: string) => {
    switch (field) {
      case 'supplier':
        setFormData(prev => ({ ...prev, supplier: '' }));
        setSupplierSearch('');
        break;
      case 'transactionDate':
        setFormData(prev => ({ ...prev, transactionDate: '' }));
        break;
      case 'dueDate':
        setFormData(prev => ({ ...prev, dueDate: '' }));
        break;
      case 'warehouse':
        setFormData(prev => ({ ...prev, warehouse: '' }));
        break;
      case 'receiveDate':
        setFormData(prev => ({ ...prev, receiveDate: '' }));
        break;
      case 'invoiceNumber':
        setFormData(prev => ({ ...prev, invoiceNumber: '' }));
        break;
      case 'referenceNumber':
        setFormData(prev => ({ ...prev, referenceNumber: '' }));
        break;
      case 'notes':
        setFormData(prev => ({ ...prev, notes: '' }));
        break;
      default:
        break;
    }
  };

  // Round 20: Open FieldInputBar - focus hidden input SYNCHRONOUSLY for Safari keyboard
  const openFieldInput = (field: string, initialValue: string = '') => {
    // CRITICAL: Focus hidden input IMMEDIATELY (synchronous with user tap)
    // This triggers Safari keyboard within gesture window
    hiddenInputRef.current?.focus();

    // First expand the field
    setExpandedFields(prev => ({ ...prev, [field]: true }));

    // Wait for expand animation to complete before showing input bar
    setTimeout(() => {
      setActiveInputField(field);
      setInputBarValue(initialValue);
    }, 80); // Wait for animation
  };

  // Round 9: Submit value from FieldInputBar
  const submitFieldInput = () => {
    if (!activeInputField) return;

    switch (activeInputField) {
      case 'supplier':
        setFormData(prev => ({ ...prev, supplier: inputBarValue }));
        setSupplierSearch('');
        break;
      case 'transactionDate':
        setFormData(prev => ({ ...prev, transactionDate: inputBarValue }));
        break;
      case 'invoiceNumber':
        setFormData(prev => ({ ...prev, invoiceNumber: inputBarValue }));
        break;
      case 'referenceNumber':
        setFormData(prev => ({ ...prev, referenceNumber: inputBarValue }));
        break;
      case 'notes':
        setFormData(prev => ({ ...prev, notes: inputBarValue }));
        break;
      default:
        break;
    }

    setActiveInputField(null);
    setInputBarValue('');
  };

  // Round 9: Close FieldInputBar without saving
  const closeFieldInput = () => {
    setActiveInputField(null);
    setInputBarValue('');
  };

  // Round 20: Lock scroll when FieldInputBar is active
  useEffect(() => {
    if (!scrollContainerRef.current) return;

    if (activeInputField) {
      // Lock scroll saat input aktif - prevent page from moving
      scrollContainerRef.current.style.overflow = 'hidden';
    } else {
      // Restore scroll saat input tidak aktif
      scrollContainerRef.current.style.overflow = 'auto';
    }
  }, [activeInputField]);

  const toggleItem = (itemId: string) => {
    setExpandedItemIds(prev => {
      const next = new Set(prev);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  };

  // Check for draft on open
  useEffect(() => {
    if (isOpen) {
      const savedDraft = localStorage.getItem(DRAFT_KEY);
      if (savedDraft) {
        try {
          const draft = JSON.parse(savedDraft);
          if (Date.now() - draft.timestamp < 24 * 60 * 60 * 1000) {
            setShowDraftPrompt(true);
          } else {
            localStorage.removeItem(DRAFT_KEY);
          }
        } catch {
          localStorage.removeItem(DRAFT_KEY);
        }
      }
      setSubmitStatus('idle');
      setErrors({});
      setItemErrors({});
    }
  }, [isOpen]);

  // Resume draft
  const handleResumeDraft = () => {
    const savedDraft = localStorage.getItem(DRAFT_KEY);
    if (savedDraft) {
      const draft = JSON.parse(savedDraft);
      setFormData(draft.data);
      setSupplierSearch(draft.data.supplier);
    }
    setShowDraftPrompt(false);
  };

  // Discard draft
  const handleDiscardDraft = () => {
    localStorage.removeItem(DRAFT_KEY);
    setShowDraftPrompt(false);
    setFormData(getInitialFormData());
    setSupplierSearch('');
  };

  // Save draft periodically
  useEffect(() => {
    if (isOpen && formData.supplier) {
      const timer = setTimeout(() => {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({
          data: formData,
          timestamp: Date.now(),
        }));
      }, 1000);
      return () => clearTimeout(timer);
    }
  }, [isOpen, formData]);

  // Round 22: Prefetch ALL suppliers on mount for INSTANT autocomplete
  useEffect(() => {
    const prefetchSuppliers = async () => {
      setIsLoadingSuppliers(true);
      const startTime = performance.now();
      console.log('[PurchaseInvoiceForm] Prefetching suppliers...');

      try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/suppliers/all?limit=500', {
          headers: { 'Authorization': `Bearer ${token}` },
        });

        if (response.ok) {
          const data = await response.json();
          const fetchTime = performance.now() - startTime;
          console.log(`[PurchaseInvoiceForm] Fetched ${data.length} suppliers in ${fetchTime.toFixed(0)}ms`);

          // Convert to SupplierSuggestion format
          const suppliers: SupplierSuggestion[] = data.map((s: { name: string; contact?: string }) => ({
            name: s.name,
            usage_count: 1, // Not used for display
          }));

          setAllSuppliers(suppliers);

          // Initialize Fuse.js for instant fuzzy search
          const fuse = new Fuse(suppliers, {
            keys: ['name'],
            threshold: 0.3,      // 0 = exact, 1 = match anything
            distance: 100,       // How far to search for match
            minMatchCharLength: 1,
          });
          setSupplierFuse(fuse);
          setSuppliersReady(true);

          const totalTime = performance.now() - startTime;
          console.log(`[PurchaseInvoiceForm] Suppliers ready in ${totalTime.toFixed(0)}ms`);
        }
      } catch (error) {
        console.error('[PurchaseInvoiceForm] Failed to prefetch suppliers:', error);
      } finally {
        setIsLoadingSuppliers(false);
      }
    };

    prefetchSuppliers();
  }, []);

  // Round 22: INSTANT client-side filtering with Fuse.js (NO API CALL!)
  // Round 22b: Only show suggestions after user types at least 1 character
  useEffect(() => {
    if (!suppliersReady || !supplierFuse) {
      return;
    }

    // Don't show suggestions until user types at least 1 character
    if (!supplierSearch || supplierSearch.length < 1 || !showSupplierDropdown) {
      setSupplierSuggestions([]);
      return;
    }

    // INSTANT fuzzy search - no debounce needed!
    const results = supplierFuse.search(supplierSearch);
    setSupplierSuggestions(results.map(r => r.item).slice(0, 10));
  }, [supplierSearch, showSupplierDropdown, suppliersReady, supplierFuse, allSuppliers]);

  // Fetch product suggestions for an item
  const fetchProductSuggestions = async (itemId: string, query: string) => {
    if (query.length < 1) {
      setProductSuggestions(prev => ({ ...prev, [itemId]: [] }));
      return;
    }

    setIsLoadingProducts(prev => ({ ...prev, [itemId]: true }));
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`/api/products/search/kulakan?q=${encodeURIComponent(query)}&limit=10`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (response.ok) {
        const data = await response.json();
        setProductSuggestions(prev => ({ ...prev, [itemId]: data.suggestions || data.products || [] }));
      }
    } catch (error) {
      console.error('Failed to fetch products:', error);
    } finally {
      setIsLoadingProducts(prev => ({ ...prev, [itemId]: false }));
    }
  };

  // Handle product search change
  const handleProductSearchChange = (itemId: string, value: string) => {
    setProductSearches(prev => ({ ...prev, [itemId]: value }));

    if (productDebounceRefs.current[itemId]) {
      clearTimeout(productDebounceRefs.current[itemId]!);
    }
    if (value && showProductDropdowns[itemId]) {
      productDebounceRefs.current[itemId] = setTimeout(() => {
        fetchProductSuggestions(itemId, value);
      }, 300);
    }
  };

  // Select a product for an item
  const handleSelectProduct = (itemId: string, product: ProductSuggestion) => {
    setFormData(prev => ({
      ...prev,
      items: prev.items.map(item => {
        if (item.id === itemId) {
          const newItem = {
            ...item,
            productName: product.name,
            unit: product.unit || item.unit,
            pricePerUnit: product.last_price || 0,
          };
          newItem.subtotal = calculateItemSubtotal(newItem);
          return newItem;
        }
        return item;
      }),
    }));
    setProductSearches(prev => ({ ...prev, [itemId]: '' }));
    setShowProductDropdowns(prev => ({ ...prev, [itemId]: false }));
    // Expand the item to show details
    setExpandedItemIds(prev => new Set(prev).add(itemId));
  };

  // Add new item
  const handleAddItem = () => {
    const newItem = createEmptyItem();
    setFormData(prev => ({
      ...prev,
      items: [...prev.items, newItem],
    }));
    setExpandedItemIds(prev => new Set(prev).add(newItem.id));
  };

  // Remove item
  const handleRemoveItem = (itemId: string) => {
    if (formData.items.length <= 1) return;
    setFormData(prev => ({
      ...prev,
      items: prev.items.filter(item => item.id !== itemId),
    }));
    setExpandedItemIds(prev => {
      const next = new Set(prev);
      next.delete(itemId);
      return next;
    });
  };

  // Update item field
  const handleItemChange = (itemId: string, field: keyof PurchaseItem, value: any) => {
    setFormData(prev => ({
      ...prev,
      items: prev.items.map(item => {
        if (item.id === itemId) {
          const newItem = { ...item, [field]: value };
          newItem.subtotal = calculateItemSubtotal(newItem);
          return newItem;
        }
        return item;
      }),
    }));
  };

  // Clear optional field and collapse
  const clearOptionalField = (field: string) => {
    setFormData(prev => ({ ...prev, [field]: undefined }));
    setExpandedFields(prev => ({ ...prev, [field]: false }));
  };

  // Calculate totals
  const totals = useMemo(() => {
    const itemsSubtotal = formData.items.reduce((sum, item) => sum + item.subtotal, 0);

    let invoiceDiscountAmount = 0;
    if (formData.invoiceDiscount) {
      if (formData.invoiceDiscount.type === 'amount') {
        invoiceDiscountAmount = formData.invoiceDiscount.value;
      } else {
        invoiceDiscountAmount = itemsSubtotal * formData.invoiceDiscount.value / 100;
      }
    }

    const afterDiscount = Math.max(0, itemsSubtotal - invoiceDiscountAmount);

    let taxAmount = 0;
    if (formData.tax) {
      if (formData.tax.type === 'amount') {
        taxAmount = formData.tax.value;
      } else {
        taxAmount = afterDiscount * formData.tax.value / 100;
      }
    }

    return {
      itemsSubtotal,
      invoiceDiscountAmount,
      taxAmount,
      total: afterDiscount + taxAmount,
    };
  }, [formData.items, formData.invoiceDiscount, formData.tax]);

  // Validate form
  const validateForm = (): boolean => {
    const newErrors: PurchaseInvoiceFormErrors = {};
    const newItemErrors: Record<string, ItemErrors> = {};
    let isValid = true;

    if (!formData.supplier.trim()) {
      newErrors.supplier = 'Supplier wajib diisi';
      isValid = false;
    }

    if (!formData.transactionDate) {
      newErrors.transactionDate = 'Tanggal faktur wajib diisi';
      isValid = false;
    }

    // Validate due date
    if (!formData.dueDate) {
      newErrors.dueDate = 'Jatuh tempo wajib diisi';
      isValid = false;
    } else if (formData.transactionDate && formData.dueDate < formData.transactionDate) {
      newErrors.dueDate = 'Jatuh tempo tidak boleh sebelum tanggal faktur';
      isValid = false;
    }

    // Validate items
    formData.items.forEach(item => {
      const errors: ItemErrors = {};

      if (!item.productName.trim()) {
        errors.productName = 'Nama produk wajib diisi';
        isValid = false;
      }

      if (item.quantity <= 0) {
        errors.quantity = 'Qty harus > 0';
        isValid = false;
      }

      if (item.pricePerUnit <= 0) {
        errors.pricePerUnit = 'Harga wajib diisi';
        isValid = false;
      }

      if (Object.keys(errors).length > 0) {
        newItemErrors[item.id] = errors;
      }
    });

    setErrors(newErrors);
    setItemErrors(newItemErrors);
    return isValid;
  };

  // Handle submit
  const handleSubmit = async () => {
    if (!validateForm()) return;

    setIsSubmitting(true);
    try {
      const token = localStorage.getItem('access_token');

      const payload = {
        transaction_type: 'pembelian',
        payment_status: 'hutang',  // Always 'hutang' - creates AP record
        payment_method: 'kredit',  // Bill = kredit (triggers AP)
        is_tempo: true,            // Signal to create AP record
        due_date: formData.dueDate,  // Jatuh tempo
        supplier_name: formData.supplier,
        transaction_date: formData.transactionDate,
        receive_date: formData.receiveDate,
        warehouse: formData.warehouse,
        items: formData.items.map(item => ({
          product_name: item.productName,
          batch: item.batch,
          expiry_date: item.expiryDate,
          quantity: item.quantity,
          unit: item.unit,
          discount_type: item.discount?.type,
          discount_value: item.discount?.value,
          price_per_unit: item.pricePerUnit,
          hpp_per_unit: item.hppPerUnit || item.pricePerUnit,
          subtotal: item.subtotal,
        })),
        invoice_discount: formData.invoiceDiscount,
        tax: formData.tax,
        total_amount: totals.total,
        notes: formData.notes,
        invoice_number: formData.invoiceNumber,
        reference_number: formData.referenceNumber,
      };

      const response = await fetch('/api/transactions/purchase', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        setSubmitStatus('success');
        localStorage.removeItem(DRAFT_KEY);

        const itemCount = formData.items.length;
        const dueDateFormatted = new Date(formData.dueDate).toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' });
        const summary = `Faktur pembelian: ${itemCount} item dari ${formData.supplier}, total Rp ${formatCurrency(totals.total)}, jatuh tempo ${dueDateFormatted}`;
        onTransactionComplete?.(summary);

        setTimeout(() => {
          onClose();
          setFormData(getInitialFormData());
          setSupplierSearch('');
        }, 1500);
      } else {
        const data = await response.json();
        setErrors({ general: data.message || 'Gagal menyimpan transaksi' });
        setSubmitStatus('error');
      }
    } catch (error) {
      console.error('Submit error:', error);
      setErrors({ general: 'Terjadi kesalahan. Silakan coba lagi.' });
      setSubmitStatus('error');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Handle close
  const handleClose = () => {
    if (formData.supplier || formData.items.some(i => i.productName)) {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        data: formData,
        timestamp: Date.now(),
      }));
    }
    onClose();
  };

  if (!isOpen) return null;

  // Slide animation for inputs
  const inputAnimation = {
    initial: { height: 0, opacity: 0, marginTop: 0 },
    animate: { height: 'auto', opacity: 1, marginTop: 8 },
    exit: { height: 0, opacity: 0, marginTop: 0 },
    transition: { type: 'spring' as const, stiffness: 300, damping: 30 },
  };

  return (
    <AnimatePresence>
      {/* Full Screen Form */}
      <motion.div
        ref={modalRef}
        className="fixed inset-0 z-50 bg-white flex flex-col"
        initial={{ x: '100%' }}
        animate={{ x: 0 }}
        exit={{ x: '100%' }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      >
        {/* Round 20: Hidden input for Safari keyboard trigger - MUST be in DOM before tap */}
        <input
          ref={hiddenInputRef}
          type="text"
          inputMode="text"
          aria-hidden="true"
          tabIndex={-1}
          style={{
            position: 'absolute',
            opacity: 0,
            pointerEvents: 'none',
            width: 0,
            height: 0,
          }}
        />

        {/* Header - Two modes: Back arrow (default) or Cancel/Save (showCreateHeader) */}
        <div
          className="sticky top-0 bg-white border-b px-4 py-1 md:py-3 flex items-center z-10"
          style={{ borderColor: COLORS.gray200 }}
        >
          {showCreateHeader ? (
            /* Create mode: Cancel - Title - Save */
            <>
              {/* Cancel Button - Pill wrapper with X icon */}
              <button
                onClick={handleClose}
                className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
                style={{ backgroundColor: COLORS.gray50 }}
              >
                <svg className="w-5 h-5" style={{ color: COLORS.gray500 }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
              {/* Title - Uppercase */}
              <span className="flex-1 text-center font-bold text-base" style={{ color: COLORS.neutral800 }}>
                BUAT FAKTUR
              </span>
              {/* Save Button - Green-50 pill */}
              <button
                onClick={() => {
                  // Trigger form submission
                  const form = document.getElementById('purchase-invoice-form');
                  if (form) {
                    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                  }
                }}
                className="px-4 h-9 rounded-full flex items-center justify-center flex-shrink-0"
                style={{ backgroundColor: '#F0FDF4' }}
              >
                <span className="text-sm font-medium" style={{ color: COLORS.neutral800 }}>Save</span>
              </button>
            </>
          ) : (
            /* Default mode: Back arrow - Title */
            <>
              <button
                onClick={handleClose}
                className="flex items-center justify-center"
                aria-label="Back"
              >
                <svg
                  width="42"
                  height="42"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="text-neutral-500"
                >
                  <polyline points="15 18 9 12 15 6" />
                </svg>
              </button>
              <span className="text-sm font-semibold tracking-wide" style={{ color: COLORS.neutral800 }}>
                FAKTUR PEMBELIAN
              </span>
            </>
          )}
        </div>

        {/* Draft Prompt */}
        {showDraftPrompt && (
          <div className="p-4 border-b" style={{ backgroundColor: COLORS.greenLight, borderColor: COLORS.gray200 }}>
            <p className="text-sm mb-2" style={{ color: COLORS.gray700 }}>
              Ada draft yang belum selesai. Lanjutkan?
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleResumeDraft}
                className="px-3 py-1.5 text-sm font-medium rounded-lg text-white"
                style={{ backgroundColor: COLORS.green }}
              >
                Lanjutkan
              </button>
              <button
                onClick={handleDiscardDraft}
                className="px-3 py-1.5 text-sm font-medium rounded-lg"
                style={{ backgroundColor: COLORS.gray200, color: COLORS.gray700 }}
              >
                Buang
              </button>
            </div>
          </div>
        )}

        {/* Scrollable Content - Round 14: scroll-padding-top for smart scroll */}
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-y-auto px-4 py-3"
          style={{ scrollPaddingTop: '60px' }}
        >
            {/* === REQUIRED FIELDS === */}

            {/* Supplier - Round 6 UI */}
            <div ref={supplierFieldRef} className="py-4" style={{ scrollMarginTop: '60px' }}>
              <div className="flex items-center justify-between">
                <LabelChip
                  type="required"
                  label="Supplier atau vendor"
                  icon={Icons.Building}
                  isExpanded={expandedFields.supplier}
                  hasValue={!!formData.supplier}
                  onToggle={() => toggleField('supplier')}
                />
                {!expandedFields.supplier && (
                  <ActionIcon
                    isExpanded={expandedFields.supplier}
                    hasValue={!!formData.supplier}
                    onClick={() => {
                      // Round 11: If has value (check icon), just expand to show value
                      // If no value (plus icon), trigger keyboard + FieldInputBar
                      if (formData.supplier) {
                        toggleField('supplier');
                      } else {
                        openFieldInput('supplier');
                      }
                    }}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.supplier && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="relative flex-1">
                        {formData.supplier ? (
                          // Filled state: rounded pill with shadow
                          <FilledValuePill value={formData.supplier} />
                        ) : (
                          // Round 9: Show placeholder - actual input is in FieldInputBar at bottom
                          <div
                            onClick={() => openFieldInput('supplier')}
                            className="w-full px-4 py-2.5 border-2 border-dashed rounded-lg text-sm cursor-pointer"
                            style={{
                              borderColor: errors.supplier ? COLORS.red : COLORS.gray300,
                              color: COLORS.gray400,
                            }}
                          >
                            Apa nama penyedia produk dan jasa?
                          </div>
                        )}
                        {errors.supplier && (
                          <p className="text-xs mt-1" style={{ color: COLORS.red }}>{errors.supplier}</p>
                        )}
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.supplier}
                        hasValue={!!formData.supplier}
                        onClick={() => toggleField('supplier')}
                        onClear={() => clearField('supplier')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Harga Termasuk Pajak - Field with toggle on right */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="Harga termasuk pajak"
                  icon={Icons.Tag}
                />
                {/* Toggle switch instead of + icon */}
                <button
                  type="button"
                  onClick={() => setFormData(prev => ({ ...prev, taxInclusive: !prev.taxInclusive }))}
                  className="w-12 h-7 rounded-full transition-colors relative"
                  style={{ backgroundColor: formData.taxInclusive ? COLORS.green : COLORS.gray300 }}
                >
                  <div
                    className="absolute top-1 w-5 h-5 bg-white rounded-full shadow transition-transform"
                    style={{
                      transform: formData.taxInclusive ? 'translateX(24px)' : 'translateX(4px)',
                    }}
                  />
                </button>
              </div>
            </div>

            {/* Tipe Diskon - Field with expandable checkbox pills */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="Tipe diskon"
                  icon={Icons.Percent}
                  isExpanded={expandedFields.discountType}
                  hasValue={formData.discountTransaction || formData.discountItem}
                  onToggle={() => toggleField('discountType')}
                />
                {!expandedFields.discountType && (
                  <ActionIcon
                    isExpanded={expandedFields.discountType}
                    hasValue={formData.discountTransaction || formData.discountItem}
                    onClick={() => toggleField('discountType')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.discountType && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      {/* Checkbox pill - Di level faktur */}
                      <button
                        type="button"
                        onClick={() => setFormData(prev => ({ ...prev, discountTransaction: !prev.discountTransaction }))}
                        className="flex items-center gap-2 px-4 py-2.5 rounded-full text-sm"
                        style={{
                          backgroundColor: 'white',
                          border: `2px dashed ${COLORS.gray300}`,
                        }}
                      >
                        <div
                          className="w-4 h-4 rounded border flex items-center justify-center"
                          style={{
                            backgroundColor: formData.discountTransaction ? COLORS.green : 'transparent',
                            borderColor: formData.discountTransaction ? COLORS.green : COLORS.gray300,
                          }}
                        >
                          {formData.discountTransaction && (
                            <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                            </svg>
                          )}
                        </div>
                        <span style={{ color: COLORS.neutral800 }}>Di level faktur</span>
                      </button>

                      {/* Checkbox pill - Di level item */}
                      <button
                        type="button"
                        onClick={() => setFormData(prev => ({ ...prev, discountItem: !prev.discountItem }))}
                        className="flex items-center gap-2 px-4 py-2.5 rounded-full text-sm"
                        style={{
                          backgroundColor: 'white',
                          border: `2px dashed ${COLORS.gray300}`,
                        }}
                      >
                        <div
                          className="w-4 h-4 rounded border flex items-center justify-center"
                          style={{
                            backgroundColor: formData.discountItem ? COLORS.green : 'transparent',
                            borderColor: formData.discountItem ? COLORS.green : COLORS.gray300,
                          }}
                        >
                          {formData.discountItem && (
                            <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                            </svg>
                          )}
                        </div>
                        <span style={{ color: COLORS.neutral800 }}>Di level item</span>
                      </button>

                      <ActionIcon
                        isExpanded={expandedFields.discountType}
                        hasValue={formData.discountTransaction || formData.discountItem}
                        onClick={() => toggleField('discountType')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Tanggal Bayar - Round 21: Consistent styling with FilledValuePill, tap triggers picker */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="required"
                  label="Tanggal faktur"
                  icon={Icons.Calendar}
                  isExpanded={expandedFields.transactionDate}
                  hasValue={!!formData.transactionDate}
                  onToggle={() => toggleField('transactionDate')}
                />
                {!expandedFields.transactionDate && (
                  <ActionIcon
                    isExpanded={expandedFields.transactionDate}
                    hasValue={!!formData.transactionDate}
                    onClick={() => {
                      // Round 21: If no value, expand and trigger date picker
                      if (!formData.transactionDate) {
                        toggleField('transactionDate');
                        // Delay to wait for expand animation, then trigger picker
                        setTimeout(() => {
                          dateInputRef.current?.showPicker?.();
                          dateInputRef.current?.click();
                        }, 150);
                      } else {
                        toggleField('transactionDate');
                      }
                    }}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.transactionDate && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1 relative">
                        {/* Round 21: Always use FilledValuePill style, tap triggers picker */}
                        <div
                          onClick={() => {
                            dateInputRef.current?.showPicker?.();
                            dateInputRef.current?.click();
                          }}
                          className="px-4 py-2.5 rounded-lg cursor-pointer"
                          style={{
                            backgroundColor: COLORS.white,
                            border: `1px solid ${COLORS.gray300}`,
                          }}
                        >
                          <span
                            className="text-sm text-left block"
                            style={{ color: formData.transactionDate ? COLORS.neutral800 : COLORS.gray400 }}
                          >
                            {formData.transactionDate
                              ? new Date(formData.transactionDate).toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' })
                              : 'Pilih tanggal...'}
                          </span>
                        </div>
                        {/* Hidden date input */}
                        <input
                          ref={dateInputRef}
                          type="date"
                          value={formData.transactionDate}
                          onChange={(e) => setFormData(prev => ({ ...prev, transactionDate: e.target.value }))}
                          className="sr-only"
                          style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                        />
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.transactionDate}
                        hasValue={!!formData.transactionDate}
                        onClick={() => toggleField('transactionDate')}
                        onClear={() => clearField('transactionDate')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Jatuh Tempo (Due Date) - Date picker */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="required"
                  label="Jatuh tempo"
                  icon={Icons.Calendar}
                  isExpanded={expandedFields.dueDate}
                  hasValue={!!formData.dueDate}
                  onToggle={() => toggleField('dueDate')}
                />
                {!expandedFields.dueDate && (
                  <ActionIcon
                    isExpanded={expandedFields.dueDate}
                    hasValue={!!formData.dueDate}
                    onClick={() => {
                      if (!formData.dueDate) {
                        toggleField('dueDate');
                        setTimeout(() => {
                          dueDateInputRef.current?.showPicker?.();
                          dueDateInputRef.current?.click();
                        }, 150);
                      } else {
                        toggleField('dueDate');
                      }
                    }}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.dueDate && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1 relative">
                        <div
                          onClick={() => {
                            dueDateInputRef.current?.showPicker?.();
                            dueDateInputRef.current?.click();
                          }}
                          className="px-4 py-2.5 rounded-lg cursor-pointer"
                          style={{
                            backgroundColor: COLORS.white,
                            border: `1px solid ${COLORS.gray300}`,
                          }}
                        >
                          <span
                            className="text-sm text-left block"
                            style={{ color: formData.dueDate ? COLORS.neutral800 : COLORS.gray400 }}
                          >
                            {formData.dueDate
                              ? new Date(formData.dueDate).toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' })
                              : 'Pilih tanggal...'}
                          </span>
                        </div>
                        {/* Hidden date input */}
                        <input
                          ref={dueDateInputRef}
                          type="date"
                          value={formData.dueDate}
                          onChange={(e) => setFormData(prev => ({ ...prev, dueDate: e.target.value }))}
                          className="sr-only"
                          style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                        />
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.dueDate}
                        hasValue={!!formData.dueDate}
                        onClick={() => toggleField('dueDate')}
                        onClear={() => clearField('dueDate')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Items Section - Round 6 UI */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="required"
                  label="Item"
                  icon={Icons.ShoppingCart}
                  isExpanded={expandedFields.items}
                  hasValue={formData.items.length > 0 && !!formData.items[0]?.productName}
                  onToggle={() => toggleField('items')}
                />
                {!expandedFields.items && (
                  <ActionIcon
                    isExpanded={expandedFields.items}
                    hasValue={formData.items.length > 0 && !!formData.items[0]?.productName}
                    onClick={() => toggleField('items')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.items && (
                  <motion.div {...inputAnimation} className="overflow-hidden space-y-3">
                    {formData.items.map((item, index) => (
                      <div
                        key={item.id}
                        className="border rounded-lg bg-white overflow-hidden"
                        style={{ borderColor: itemErrors[item.id] ? COLORS.red : COLORS.gray200 }}
                      >
                        {/* Item Header */}
                        <div
                          className="flex items-center justify-between px-3 py-2.5 cursor-pointer"
                          style={{ backgroundColor: COLORS.gray50 }}
                          onClick={() => toggleItem(item.id)}
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium" style={{ color: COLORS.gray700 }}>
                              {index + 1}. {item.productName || 'Nama produk/jasa'}
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            {formData.items.length > 1 && (
                              <button
                                type="button"
                                onClick={(e) => { e.stopPropagation(); handleRemoveItem(item.id); }}
                                className="w-6 h-6 flex items-center justify-center rounded-full hover:bg-red-50"
                              >
                                <svg className="w-4 h-4" fill="none" stroke={COLORS.red} viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                </svg>
                              </button>
                            )}
                            <ChevronIcon isExpanded={expandedItemIds.has(item.id)} />
                          </div>
                        </div>

                        {/* Item Details */}
                        <AnimatePresence>
                          {expandedItemIds.has(item.id) && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: 'auto', opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              transition={{ type: 'spring', stiffness: 300, damping: 30 }}
                              className="overflow-hidden"
                            >
                              <div className="px-3 py-3 space-y-3 border-t" style={{ borderColor: COLORS.gray100 }}>
                                {/* Product Name Search */}
                                <div className="relative">
                                  <input
                                    type="text"
                                    value={item.productName || productSearches[item.id] || ''}
                                    onChange={(e) => {
                                      handleProductSearchChange(item.id, e.target.value);
                                      handleItemChange(item.id, 'productName', e.target.value);
                                      setShowProductDropdowns(prev => ({ ...prev, [item.id]: true }));
                                    }}
                                    onFocus={() => setShowProductDropdowns(prev => ({ ...prev, [item.id]: true }))}
                                    onBlur={() => setTimeout(() => setShowProductDropdowns(prev => ({ ...prev, [item.id]: false })), 200)}
                                    placeholder="Nama produk/jasa"
                                    className="w-full px-3 py-2 border rounded-lg text-sm"
                                    style={{ borderColor: itemErrors[item.id]?.productName ? COLORS.red : COLORS.gray200 }}
                                  />
                                  {itemErrors[item.id]?.productName && (
                                    <p className="text-xs mt-1" style={{ color: COLORS.red }}>{itemErrors[item.id].productName}</p>
                                  )}

                                  {/* Product Dropdown */}
                                  {showProductDropdowns[item.id] && (productSuggestions[item.id]?.length || 0) > 0 && (
                                    <div className="absolute z-20 w-full mt-1 bg-white border rounded-lg shadow-lg max-h-48 overflow-y-auto">
                                      {productSuggestions[item.id].map((p, i) => (
                                        <button
                                          key={i}
                                          className="w-full px-3 py-2 text-left text-sm hover:bg-gray-50 flex justify-between"
                                          onMouseDown={() => handleSelectProduct(item.id, p)}
                                        >
                                          <span>{p.name}</span>
                                          {p.last_price && (
                                            <span style={{ color: COLORS.gray500 }}>Rp {formatCurrency(p.last_price)}</span>
                                          )}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>

                                {/* Detail rows */}
                                <div className="space-y-2">
                                  {/* Batch */}
                                  <div className="flex justify-between items-center py-1.5 border-b" style={{ borderColor: COLORS.gray100 }}>
                                    <span className="text-sm" style={{ color: COLORS.gray500 }}>Batch</span>
                                    <input
                                      type="text"
                                      value={item.batch || ''}
                                      onChange={(e) => handleItemChange(item.id, 'batch', e.target.value)}
                                      placeholder="-"
                                      className="text-right text-sm border-0 bg-transparent focus:outline-none w-32"
                                    />
                                  </div>

                                  {/* Expired Date */}
                                  <div className="flex justify-between items-center py-1.5 border-b" style={{ borderColor: COLORS.gray100 }}>
                                    <span className="text-sm" style={{ color: COLORS.gray500 }}>Expired Date</span>
                                    <input
                                      type="date"
                                      value={item.expiryDate || ''}
                                      onChange={(e) => handleItemChange(item.id, 'expiryDate', e.target.value)}
                                      className="text-right text-sm border-0 bg-transparent focus:outline-none"
                                    />
                                  </div>

                                  {/* Quantity */}
                                  <div className="flex justify-between items-center py-1.5 border-b" style={{ borderColor: COLORS.gray100 }}>
                                    <span className="text-sm" style={{ color: COLORS.gray500 }}>Quantity</span>
                                    <div className="flex items-center gap-2">
                                      <input
                                        type="number"
                                        inputMode="numeric"
                                        value={item.quantity}
                                        onChange={(e) => handleItemChange(item.id, 'quantity', parseInt(e.target.value) || 0)}
                                        className="text-right text-sm border-0 bg-transparent focus:outline-none w-16"
                                        style={{ color: itemErrors[item.id]?.quantity ? COLORS.red : undefined }}
                                      />
                                      <select
                                        value={item.unit}
                                        onChange={(e) => handleItemChange(item.id, 'unit', e.target.value)}
                                        className="text-sm border-0 bg-transparent focus:outline-none"
                                        style={{ color: COLORS.gray700 }}
                                      >
                                        {UNIT_OPTIONS.map(u => (
                                          <option key={u} value={u}>{u}</option>
                                        ))}
                                      </select>
                                    </div>
                                  </div>

                                  {/* Discount */}
                                  <div className="flex justify-between items-center py-1.5 border-b" style={{ borderColor: COLORS.gray100 }}>
                                    <span className="text-sm" style={{ color: COLORS.gray500 }}>Discount</span>
                                    <div className="flex items-center gap-1">
                                      <input
                                        type="number"
                                        inputMode="numeric"
                                        value={item.discount?.value || ''}
                                        onChange={(e) => {
                                          const val = parseInt(e.target.value) || 0;
                                          handleItemChange(item.id, 'discount', val ? { type: 'percentage', value: val } : undefined);
                                        }}
                                        placeholder="-"
                                        className="text-right text-sm border-0 bg-transparent focus:outline-none w-12"
                                      />
                                      <span className="text-sm" style={{ color: COLORS.gray500 }}>%</span>
                                    </div>
                                  </div>

                                  {/* Harga satuan */}
                                  <div className="flex justify-between items-center py-1.5 border-b" style={{ borderColor: COLORS.gray100 }}>
                                    <span className="text-sm" style={{ color: COLORS.gray500 }}>Harga satuan</span>
                                    <div className="flex items-center gap-1">
                                      <span className="text-sm" style={{ color: COLORS.gray500 }}>Rp</span>
                                      <input
                                        type="text"
                                        inputMode="numeric"
                                        value={formatCurrency(item.pricePerUnit)}
                                        onChange={(e) => handleItemChange(item.id, 'pricePerUnit', parseCurrency(e.target.value))}
                                        className="text-right text-sm border-0 bg-transparent focus:outline-none w-24"
                                        style={{ color: itemErrors[item.id]?.pricePerUnit ? COLORS.red : undefined }}
                                      />
                                    </div>
                                  </div>

                                  {/* Jumlah (Subtotal) */}
                                  <div className="flex justify-between items-center py-1.5">
                                    <span className="text-sm font-medium" style={{ color: COLORS.gray700 }}>Jumlah</span>
                                    <span className="text-sm font-semibold" style={{ color: COLORS.green }}>
                                      Rp {formatCurrency(item.subtotal)}
                                    </span>
                                  </div>
                                </div>
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>
                    ))}

                    {/* Add Item Button */}
                    <button
                      type="button"
                      onClick={handleAddItem}
                      className="flex items-center justify-center gap-2 w-full py-2.5 text-sm font-medium rounded-lg"
                      style={{ color: COLORS.green }}
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      Tambah item
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Gudang Tujuan - Conditional: only show if items have product names */}
            {formData.items.some(item => item.productName && item.productName.trim() !== '') && (
              <div className="py-4">
                <div className="flex items-center justify-between">
                  <LabelChip
                    type="required"
                    label="Gudang tujuan"
                    icon={Icons.Warehouse}
                    isExpanded={expandedFields.warehouse}
                    hasValue={!!formData.warehouse}
                    onToggle={() => toggleField('warehouse')}
                  />
                  {!expandedFields.warehouse && (
                    <ActionIcon
                      isExpanded={expandedFields.warehouse}
                      hasValue={!!formData.warehouse}
                      onClick={() => toggleField('warehouse')}
                    />
                  )}
                </div>
                <AnimatePresence>
                  {expandedFields.warehouse && (
                    <motion.div {...inputAnimation} className="overflow-hidden">
                      <div className="flex items-center gap-2 mt-3">
                        <div className="flex-1">
                          {formData.warehouse ? (
                            <FilledValuePill
                              value={WAREHOUSE_OPTIONS.find(o => o.value === formData.warehouse)?.label || formData.warehouse}
                            />
                          ) : (
                            <div className="flex flex-wrap gap-2">
                              {WAREHOUSE_OPTIONS.map(opt => (
                                <button
                                  key={opt.value}
                                  type="button"
                                  onClick={() => setFormData(prev => ({ ...prev, warehouse: opt.value }))}
                                  className="px-4 py-2.5 rounded-full text-sm font-medium transition-all hover:shadow-sm active:scale-95"
                                  style={{
                                    backgroundColor: 'transparent',
                                    color: COLORS.neutral800,
                                    border: `2px dashed ${COLORS.gray300}`,
                                  }}
                                >
                                  {opt.label}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                        <ActionIcon
                          isExpanded={expandedFields.warehouse}
                          hasValue={!!formData.warehouse}
                          onClick={() => toggleField('warehouse')}
                          onClear={() => clearField('warehouse')}
                        />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}

            {/* === OPTIONAL FIELDS === */}

            {/* No. faktur - Round 6 UI */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="No. faktur"
                  icon={Icons.Document}
                  isExpanded={expandedFields.invoiceNumber}
                  hasValue={!!formData.invoiceNumber}
                  onToggle={() => toggleField('invoiceNumber')}
                />
                {!expandedFields.invoiceNumber && (
                  <ActionIcon
                    isExpanded={expandedFields.invoiceNumber}
                    hasValue={!!formData.invoiceNumber}
                    onClick={() => toggleField('invoiceNumber')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.invoiceNumber && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1">
                        {formData.invoiceNumber ? (
                          <FilledValuePill value={formData.invoiceNumber} />
                        ) : (
                          <input
                            type="text"
                            value={formData.invoiceNumber || ''}
                            onChange={(e) => setFormData(prev => ({ ...prev, invoiceNumber: e.target.value }))}
                            placeholder="INV-001"
                            className="w-full px-4 py-2.5 border-2 border-dashed rounded-lg text-sm focus:outline-none focus:border-gray-400"
                            style={{ borderColor: COLORS.gray300 }}
                          />
                        )}
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.invoiceNumber}
                        hasValue={!!formData.invoiceNumber}
                        onClick={() => toggleField('invoiceNumber')}
                        onClear={() => clearField('invoiceNumber')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* No. referensi - Round 6 UI */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="No. referensi"
                  icon={Icons.Tag}
                  isExpanded={expandedFields.referenceNumber}
                  hasValue={!!formData.referenceNumber}
                  onToggle={() => toggleField('referenceNumber')}
                />
                {!expandedFields.referenceNumber && (
                  <ActionIcon
                    isExpanded={expandedFields.referenceNumber}
                    hasValue={!!formData.referenceNumber}
                    onClick={() => toggleField('referenceNumber')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.referenceNumber && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1">
                        {formData.referenceNumber ? (
                          <FilledValuePill value={formData.referenceNumber} />
                        ) : (
                          <input
                            type="text"
                            value={formData.referenceNumber || ''}
                            onChange={(e) => setFormData(prev => ({ ...prev, referenceNumber: e.target.value }))}
                            placeholder="PO-001"
                            className="w-full px-4 py-2.5 border-2 border-dashed rounded-lg text-sm focus:outline-none focus:border-gray-400"
                            style={{ borderColor: COLORS.gray300 }}
                          />
                        )}
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.referenceNumber}
                        hasValue={!!formData.referenceNumber}
                        onClick={() => toggleField('referenceNumber')}
                        onClear={() => clearField('referenceNumber')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Diskon faktur - Round 6 UI */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="Diskon faktur"
                  icon={Icons.Percent}
                  isExpanded={expandedFields.invoiceDiscount}
                  hasValue={!!formData.invoiceDiscount?.value}
                  onToggle={() => toggleField('invoiceDiscount')}
                />
                {!expandedFields.invoiceDiscount && (
                  <ActionIcon
                    isExpanded={expandedFields.invoiceDiscount}
                    hasValue={!!formData.invoiceDiscount?.value}
                    onClick={() => toggleField('invoiceDiscount')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.invoiceDiscount && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1">
                        {formData.invoiceDiscount?.value ? (
                          <FilledValuePill value={`${formData.invoiceDiscount.value}%`} />
                        ) : (
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              inputMode="numeric"
                              value={formData.invoiceDiscount?.value || ''}
                              onChange={(e) => {
                                const val = parseInt(e.target.value) || 0;
                                setFormData(prev => ({
                                  ...prev,
                                  invoiceDiscount: val ? { type: 'percentage', value: val } : undefined,
                                }));
                              }}
                              placeholder="0"
                              className="flex-1 px-4 py-2.5 border-2 border-dashed rounded-lg text-sm focus:outline-none focus:border-gray-400"
                              style={{ borderColor: COLORS.gray300 }}
                            />
                            <span className="text-sm" style={{ color: COLORS.gray500 }}>%</span>
                          </div>
                        )}
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.invoiceDiscount}
                        hasValue={!!formData.invoiceDiscount?.value}
                        onClick={() => toggleField('invoiceDiscount')}
                        onClear={() => setFormData(prev => ({ ...prev, invoiceDiscount: undefined }))}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Pajak - Round 6 UI */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="Pajak"
                  icon={Icons.Receipt}
                  isExpanded={expandedFields.tax}
                  hasValue={!!formData.tax?.value}
                  onToggle={() => toggleField('tax')}
                />
                {!expandedFields.tax && (
                  <ActionIcon
                    isExpanded={expandedFields.tax}
                    hasValue={!!formData.tax?.value}
                    onClick={() => toggleField('tax')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.tax && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1">
                        {formData.tax?.value ? (
                          <FilledValuePill value={`${formData.tax.value}%`} />
                        ) : (
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              inputMode="numeric"
                              value={formData.tax?.value || ''}
                              onChange={(e) => {
                                const val = parseInt(e.target.value) || 0;
                                setFormData(prev => ({
                                  ...prev,
                                  tax: val ? { type: 'percentage', value: val } : undefined,
                                }));
                              }}
                              placeholder="0"
                              className="flex-1 px-4 py-2.5 border-2 border-dashed rounded-lg text-sm focus:outline-none focus:border-gray-400"
                              style={{ borderColor: COLORS.gray300 }}
                            />
                            <span className="text-sm" style={{ color: COLORS.gray500 }}>%</span>
                          </div>
                        )}
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.tax}
                        hasValue={!!formData.tax?.value}
                        onClick={() => toggleField('tax')}
                        onClear={() => setFormData(prev => ({ ...prev, tax: undefined }))}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Catatan - Round 6 UI */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="Catatan"
                  icon={Icons.PencilSquare}
                  isExpanded={expandedFields.notes}
                  hasValue={!!formData.notes}
                  onToggle={() => toggleField('notes')}
                />
                {!expandedFields.notes && (
                  <ActionIcon
                    isExpanded={expandedFields.notes}
                    hasValue={!!formData.notes}
                    onClick={() => toggleField('notes')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.notes && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div className="flex-1">
                        {formData.notes ? (
                          <FilledValuePill value={formData.notes} />
                        ) : (
                          <textarea
                            value={formData.notes || ''}
                            onChange={(e) => setFormData(prev => ({ ...prev, notes: e.target.value }))}
                            placeholder="Catatan tambahan..."
                            rows={2}
                            className="w-full px-4 py-2.5 border-2 border-dashed rounded-lg text-sm resize-none focus:outline-none focus:border-gray-400"
                            style={{ borderColor: COLORS.gray300 }}
                          />
                        )}
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.notes}
                        hasValue={!!formData.notes}
                        onClick={() => toggleField('notes')}
                        onClear={() => clearField('notes')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Lampiran - Round 6 UI (placeholder) */}
            <div className="py-4">
              <div className="flex items-center justify-between">
                <LabelChip
                  type="optional"
                  label="Lampiran"
                  icon={Icons.Paperclip}
                  isExpanded={expandedFields.attachments}
                  hasValue={false}
                  onToggle={() => toggleField('attachments')}
                />
                {!expandedFields.attachments && (
                  <ActionIcon
                    isExpanded={expandedFields.attachments}
                    hasValue={false}
                    onClick={() => toggleField('attachments')}
                  />
                )}
              </div>
              <AnimatePresence>
                {expandedFields.attachments && (
                  <motion.div {...inputAnimation} className="overflow-hidden">
                    <div className="flex items-center gap-2 mt-3">
                      <div
                        className="flex-1 flex items-center justify-center p-4 border-2 border-dashed rounded-lg"
                        style={{ borderColor: COLORS.gray300, backgroundColor: COLORS.gray50 }}
                      >
                        <span className="text-sm" style={{ color: COLORS.gray400 }}>
                          Fitur upload lampiran segera hadir
                        </span>
                      </div>
                      <ActionIcon
                        isExpanded={expandedFields.attachments}
                        hasValue={false}
                        onClick={() => toggleField('attachments')}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Total Pembayaran - Calculated - At bottom */}
            <div className="py-4">
              <LabelChip
                type="calculated"
                label="Total Pembayaran"
                icon={Icons.Calculator}
              />
              <div className="mt-3 p-4 rounded-xl" style={{ backgroundColor: COLORS.gray50 }}>
                <div className="flex justify-between items-center">
                  <span className="text-sm" style={{ color: COLORS.gray500 }}>Kalkulasi otomatis</span>
                  <span className="text-xl font-bold" style={{ color: COLORS.green }}>
                    Rp {formatCurrency(totals.total)}
                  </span>
                </div>
              </div>
            </div>

            {/* Error message */}
            {errors.general && (
              <div className="p-3 rounded-lg mt-3" style={{ backgroundColor: COLORS.redLight }}>
                <p className="text-sm" style={{ color: COLORS.red }}>{errors.general}</p>
              </div>
            )}

          {/* Success message */}
          {submitStatus === 'success' && (
            <div className="p-3 rounded-lg mt-3" style={{ backgroundColor: COLORS.greenLight }}>
              <p className="text-sm text-center" style={{ color: COLORS.green }}>
                Transaksi berhasil disimpan!
              </p>
            </div>
          )}
        </div>

        {/* Round 13: FieldInputBar - Bottom input bar with scroll container ref */}
        {activeInputField === 'supplier' && (
          <FieldInputBar
            fieldIcon={Icons.Building}
            fieldType="required"
            value={inputBarValue}
            onChange={(val) => {
              setInputBarValue(val);
              // Trigger supplier autocomplete
              setSupplierSearch(val);
              setShowSupplierDropdown(true);
            }}
            onSubmit={submitFieldInput}
            onClose={closeFieldInput}
            suggestions={supplierSuggestions}
            onSelectSuggestion={(name) => {
              setInputBarValue(name);
              setFormData(prev => ({ ...prev, supplier: name }));
              setActiveInputField(null);
              setInputBarValue('');
              setSupplierSearch('');
            }}
            targetFieldRef={supplierFieldRef}
            scrollContainerRef={scrollContainerRef}
          />
        )}
      </motion.div>
    </AnimatePresence>
  );
};

export default PurchaseInvoiceForm;

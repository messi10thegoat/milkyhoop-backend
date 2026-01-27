/**
 * AddItemForm - Fullscreen form for adding new items (goods/services)
 *
 * Features:
 * - Type toggle: Barang (goods) / Jasa (service)
 * - Track Inventory toggle (goods only)
 * - Unit conversions with pricing per unit
 * - Sales & Purchase info sections
 * - Barcode scanning integration
 */

import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchWithAuth } from '../../../../utils/fetchWithAuth';
import { extractErrorMessage } from '../../../../utils/extractErrorMessage';
import {
  CreateItemData,
  INITIAL_ITEM_DATA,
  UnitConversionInput,
  DEFAULT_UNITS,
  SALES_ACCOUNTS,
  PURCHASE_ACCOUNTS,
  TAX_OPTIONS,
  SERVICE_TAX_OPTIONS,
  AddItemFormProps,
} from '../../../../types/items';

// Design tokens
const COLORS = {
  bgPrimary: '#FFFFFF',
  bgCard: '#F7F6F3',
  bgCardInner: '#EFEEE9',
  bgCardDarker: '#E8E7E2',
  textPrimary: '#1A1A1A',
  textSecondary: '#6B6B6B',
  textMuted: '#9A9A9A',
  textInverse: '#FFFFFF',
  accentOlive: '#8B9A5B',
  accentOliveLight: '#E8EBD9',
  accentOliveSoft: '#C5CBA8',
  borderColor: '#E8E6E1',
  dividerColor: '#E5E5E5',
};

// Animation config
const SPRING_CONFIG = { duration: 0.3, ease: [0.32, 0.72, 0, 1] as const };

// =============================================================================
// NUMBER FORMATTING HELPERS
// =============================================================================

// Format number with thousand separator (Indonesian style: 1.000.000)
const formatNumber = (value: number | string | undefined): string => {
  if (value === undefined || value === null || value === '') return '';
  const num = typeof value === 'string' ? parseFloat(value) : value;
  if (isNaN(num) || num === 0) return '';
  return num.toLocaleString('id-ID');
};

// Parse formatted number string back to number
const parseFormattedNumber = (value: string): number => {
  if (!value) return 0;
  // Remove thousand separators (dots) and replace comma with dot for decimals
  const cleaned = value.replace(/\./g, '').replace(',', '.');
  return parseFloat(cleaned) || 0;
};

// =============================================================================
// ICONS
// =============================================================================

const BackIcon = () => (
  <svg style={{ width: '22px', height: '22px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M15.75 19.5L8.25 12l7.5-7.5" />
  </svg>
);

const PlusIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 4.5v15m7.5-7.5h-15" />
  </svg>
);

const CloseIcon = () => (
  <svg style={{ width: '14px', height: '14px', color: COLORS.textMuted }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 18L18 6M6 6l12 12" />
  </svg>
);

const SearchIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: COLORS.textMuted }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </svg>
);

const MinusIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19.5 12h-15" />
  </svg>
);

const ChevronIcon = () => (
  <svg style={{ width: '14px', height: '14px', color: COLORS.textMuted }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 18l6-6-6-6" />
  </svg>
);

const ChevronDownIcon = () => (
  <svg style={{ width: '14px', height: '14px', color: COLORS.textMuted }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
  </svg>
);

const ChevronRightIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: COLORS.textMuted }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8.25 4.5l7.5 7.5-7.5 7.5" />
  </svg>
);

const CheckIcon = () => (
  <svg style={{ width: '16px', height: '16px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4.5 12.75l6 6 9-13.5" />
  </svg>
);

const BoxIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z" />
  </svg>
);

const ToolIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
  </svg>
);

const ScanIcon = () => (
  <svg style={{ width: '18px', height: '18px' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 5v14" />
    <path d="M8 5v14" />
    <path d="M12 5v14" />
    <path d="M17 5v14" />
    <path d="M21 5v14" />
  </svg>
);

const CurrencyIcon = () => (
  <svg style={{ width: '20px', height: '20px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 6v12m-3-2.818l.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

const CartIcon = () => (
  <svg style={{ width: '20px', height: '20px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2.25 3h1.386c.51 0 .955.343 1.087.835l.383 1.437M7.5 14.25a3 3 0 00-3 3h15.75m-12.75-3h11.218c1.121-2.3 2.1-4.684 2.924-7.138a60.114 60.114 0 00-16.536-1.84M7.5 14.25L5.106 5.272M6 20.25a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm12.75 0a.75.75 0 11-1.5 0 .75.75 0 011.5 0z" />
  </svg>
);

const UnitIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6z" />
  </svg>
);

const ConversionIcon = () => (
  <svg style={{ width: '18px', height: '18px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" />
  </svg>
);

// =============================================================================
// SUBCOMPONENTS
// =============================================================================

// Toggle Switch
interface ToggleSwitchProps {
  value: boolean;
  onChange: (v: boolean) => void;
}

const ToggleSwitch: React.FC<ToggleSwitchProps> = ({ value, onChange }) => (
  <div
    onClick={() => onChange(!value)}
    style={{
      width: '48px',
      height: '28px',
      background: value ? COLORS.accentOliveSoft : COLORS.bgCardInner,
      borderRadius: '100px',
      position: 'relative',
      cursor: 'pointer',
      transition: 'background 0.2s ease',
    }}
  >
    <div style={{
      position: 'absolute',
      top: '3px',
      left: value ? '23px' : '3px',
      width: '22px',
      height: '22px',
      background: COLORS.bgPrimary,
      borderRadius: '50%',
      boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
      transition: 'left 0.2s ease',
    }} />
  </div>
);

// Type Pill Button
interface TypePillProps {
  icon: React.ReactNode;
  label?: string;
  active: boolean;
  onClick: () => void;
}

const TypePill: React.FC<TypePillProps> = ({ icon, label, active, onClick }) => (
  <button
    onClick={onClick}
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: label ? '6px' : '0',
      padding: label ? '12px 16px' : '12px 14px',
      background: active ? COLORS.accentOliveLight : COLORS.bgCard,
      borderRadius: '100px',
      border: 'none',
      fontFamily: 'inherit',
      cursor: 'pointer',
    }}
  >
    {icon}
    {label && (
      <span style={{ fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.3px' }}>
        {label}
      </span>
    )}
    {active && label && <CheckIcon />}
  </button>
);

// Stepper Component (for unit conversion)
interface StepperProps {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
}

const Stepper: React.FC<StepperProps> = ({ value, onChange, min = 1, max = 9999 }) => (
  <div style={{
    display: 'flex',
    alignItems: 'center',
    background: COLORS.bgCardInner,
    borderRadius: '100px',
    overflow: 'hidden',
  }}>
    <button
      type="button"
      onClick={() => onChange(Math.max(min, value - 1))}
      style={{
        width: '40px',
        height: '40px',
        border: 'none',
        background: 'none',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <MinusIcon />
    </button>
    <input
      type="text"
      inputMode="numeric"
      value={value}
      onChange={(e) => {
        const num = parseInt(e.target.value) || min;
        onChange(Math.min(max, Math.max(min, num)));
      }}
      style={{
        width: '50px',
        height: '40px',
        border: 'none',
        borderLeft: `1px solid ${COLORS.borderColor}`,
        borderRight: `1px solid ${COLORS.borderColor}`,
        background: 'none',
        fontFamily: 'inherit',
        fontSize: '14px',
        fontWeight: 600,
        textAlign: 'center',
        color: COLORS.textPrimary,
        outline: 'none',
      }}
    />
    <button
      type="button"
      onClick={() => onChange(Math.min(max, value + 1))}
      style={{
        width: '40px',
        height: '40px',
        border: 'none',
        background: 'none',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <PlusIcon />
    </button>
  </div>
);

// Section Label
const SectionLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{
    fontSize: '12px',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.8px',
    color: COLORS.textMuted,
    margin: '8px 0 4px 4px',
  }}>
    {children}
  </div>
);

// Toggle Row
interface ToggleRowProps {
  icon: React.ReactNode;
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  hidden?: boolean;
}

const ToggleRow: React.FC<ToggleRowProps> = ({ icon, label, value, onChange, hidden }) => {
  if (hidden) return null;
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '14px 16px',
      background: COLORS.bgCard,
      borderRadius: '16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600 }}>
        {icon}
        <span>{label}</span>
      </div>
      <ToggleSwitch value={value} onChange={onChange} />
    </div>
  );
};

// Price Input Row
interface PriceRowProps {
  label: string;
  value: number | undefined;
  onChange: (v: number | undefined) => void;
}

const PriceRow: React.FC<PriceRowProps> = ({ label, value, onChange }) => (
  <div style={{
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '10px 0',
    borderBottom: `1px solid ${COLORS.dividerColor}`,
  }}>
    <span style={{ fontSize: '13px', color: COLORS.textSecondary }}>{label}</span>
    <input
      type="text"
      inputMode="numeric"
      value={value !== undefined ? value.toLocaleString('id-ID') : ''}
      onChange={(e) => {
        const num = parseInt(e.target.value.replace(/\D/g, ''), 10);
        onChange(isNaN(num) ? undefined : num);
      }}
      placeholder="0"
      style={{
        width: '120px',
        padding: '8px 12px',
        border: `1px solid ${COLORS.borderColor}`,
        borderRadius: '12px',
        fontFamily: 'inherit',
        fontSize: '14px',
        fontWeight: 600,
        textAlign: 'right',
        outline: 'none',
      }}
    />
  </div>
);

// Form Row (for account/tax selection)
interface FormRowProps {
  label: string;
  value: string;
  onClick: () => void;
  hasValue?: boolean;
}

const FormRow: React.FC<FormRowProps> = ({ label, value, onClick, hasValue = true }) => (
  <div style={{
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 0',
    borderBottom: `1px solid ${COLORS.dividerColor}`,
  }}>
    <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>{label}</span>
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        fontSize: '14px',
        fontWeight: 600,
        color: hasValue ? COLORS.textPrimary : COLORS.textMuted,
        cursor: 'pointer',
        padding: '6px 10px',
        borderRadius: '12px',
      }}
    >
      <span>{value || 'Pilih'}</span>
      <ChevronRightIcon />
    </div>
  </div>
);

// Collapsible Section Card
interface SectionCardProps {
  title: string;
  icon: React.ReactNode;
  active: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

const SectionCard: React.FC<SectionCardProps> = ({ title, icon, active, onToggle, children }) => (
  <div style={{ background: COLORS.bgCard, borderRadius: '16px', overflow: 'hidden' }}>
    <div
      onClick={onToggle}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 16px',
        cursor: 'pointer',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        {icon}
        <span style={{ fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.3px' }}>
          {title}
        </span>
      </div>
      <ToggleSwitch value={active} onChange={onToggle} />
    </div>
    {active && (
      <div style={{ padding: '0 16px 16px' }}>
        <div style={{ height: '1px', background: COLORS.dividerColor, marginBottom: '14px' }} />
        {children}
      </div>
    )}
  </div>
);

// =============================================================================
// BOTTOM SHEET
// =============================================================================

interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

const BottomSheet: React.FC<BottomSheetProps> = ({ isOpen, onClose, title, children }) => (
  <AnimatePresence>
    {isOpen && (
      <>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={onClose}
          style={{
            position: 'fixed',
            top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.4)',
            zIndex: 150,
          }}
        />
        <motion.div
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={SPRING_CONFIG}
          style={{
            position: 'fixed',
            bottom: 0, left: 0, right: 0,
            maxWidth: '430px',
            margin: '0 auto',
            background: COLORS.bgPrimary,
            borderRadius: '24px 24px 0 0',
            padding: '12px 20px 32px',
            paddingBottom: 'calc(32px + env(safe-area-inset-bottom))',
            maxHeight: '85vh',
            overflowY: 'auto',
            zIndex: 200,
          }}
        >
          <div style={{ width: '40px', height: '4px', background: COLORS.dividerColor, borderRadius: '2px', margin: '0 auto 16px' }} />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
            <span style={{ fontSize: '18px', fontWeight: 700 }}>{title}</span>
            <button onClick={onClose} style={{
              width: '32px', height: '32px', border: 'none', background: COLORS.bgCard,
              borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
            }}>
              <CloseIcon />
            </button>
          </div>
          {children}
        </motion.div>
      </>
    )}
  </AnimatePresence>
);

// Option Item for sheets
interface OptionItemProps {
  label: string;
  selected: boolean;
  onClick: () => void;
}

const OptionItem: React.FC<OptionItemProps> = ({ label, selected, onClick }) => (
  <div
    onClick={onClick}
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '14px 0',
      borderBottom: `1px solid ${COLORS.dividerColor}`,
      cursor: 'pointer',
    }}
  >
    <span style={{ fontSize: '15px', color: selected ? COLORS.accentOlive : COLORS.textPrimary, fontWeight: selected ? 600 : 400 }}>
      {label}
    </span>
    {selected && <CheckIcon />}
  </div>
);

// =============================================================================
// MAIN COMPONENT
// =============================================================================

// Success Toast Component
const SuccessToast: React.FC<{ message: string }> = ({ message }) => (
  <motion.div
    initial={{ opacity: 0, y: 20 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, y: 20 }}
    style={{
      position: 'fixed',
      bottom: 100,
      left: '50%',
      transform: 'translateX(-50%)',
      background: '#5B8C51',
      color: '#FFFFFF',
      padding: '12px 20px',
      borderRadius: '100px',
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      fontSize: 14,
      fontWeight: 600,
      boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
      zIndex: 300,
    }}
  >
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6L9 17l-5-5"/>
    </svg>
    {message}
  </motion.div>
);

const AddItemForm: React.FC<AddItemFormProps> = ({ isOpen, onClose, onSuccess, editItem }) => {
  const [formData, setFormData] = useState<CreateItemData>(INITIAL_ITEM_DATA);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSuccessToast, setShowSuccessToast] = useState(false);

  // Edit mode detection
  const isEditMode = !!editItem?.id;

  // Expanded sections
  const [salesActive, setSalesActive] = useState(true);
  const [purchaseActive, setPurchaseActive] = useState(true);

  // Accordion states (FieldPill pattern)
  const [expandedField, setExpandedField] = useState<string | null>(null);
  const toggleField = (field: string) => {
    setExpandedField(expandedField === field ? null : field);
  };

  // Sheet states
  const [nameSheetOpen, setNameSheetOpen] = useState(false);
  const [unitSheetOpen, setUnitSheetOpen] = useState(false);
  const [salesAccountSheetOpen, setSalesAccountSheetOpen] = useState(false);
  const [purchaseAccountSheetOpen, setPurchaseAccountSheetOpen] = useState(false);
  const [salesTaxSheetOpen, setSalesTaxSheetOpen] = useState(false);
  const [purchaseTaxSheetOpen, setPurchaseTaxSheetOpen] = useState(false);

  // Unit search and custom unit
  const [unitSearchQuery, setUnitSearchQuery] = useState('');
  const [customUnitValue, setCustomUnitValue] = useState('');

  // Conversion unit sheet state (-1 means closed)
  const [conversionUnitSheetIndex, setConversionUnitSheetIndex] = useState<number>(-1);

  // New pill-based sheets
  const [conversionSheetOpen, setConversionSheetOpen] = useState(false);
  const [salesInfoSheetOpen, setSalesInfoSheetOpen] = useState(false);
  const [purchaseInfoSheetOpen, setPurchaseInfoSheetOpen] = useState(false);

  // Quantity picker state (-1 means closed)
  const [conversionQuantitySheetIndex, setConversionQuantitySheetIndex] = useState<number>(-1);
  const [customQuantityValue, setCustomQuantityValue] = useState('');

  // API-fetched dropdown options
  const [categories, setCategories] = useState<string[]>([]);
  const [taxOptionsApi, setTaxOptionsApi] = useState<{ goods: any[]; services: any[] }>({ goods: [], services: [] });
  const [salesAccountsApi, setSalesAccountsApi] = useState<{ id: string; code: string; name: string }[]>([]);
  const [purchaseAccountsApi, setPurchaseAccountsApi] = useState<{ id: string; code: string; name: string }[]>([]);
  const [vendorResults, setVendorResults] = useState<{ id: string; name: string; code?: string }[]>([]);
  const [vendorSearch, setVendorSearch] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [showNewCategoryInput, setShowNewCategoryInput] = useState(false);

  const nameInputRef = useRef<HTMLInputElement>(null);

  // Reset form when opened, or populate with editItem data
  useEffect(() => {
    if (isOpen) {
      if (editItem?.id) {
        // Edit mode - populate form with existing data
        const editItemData = editItem as any;
        setFormData({
          name: editItem.name || '',
          itemType: editItem.itemType || 'goods',
          trackInventory: editItem.trackInventory ?? false,
          baseUnit: editItem.baseUnit || 'pcs',
          barcode: editItem.barcode || '',
          kategori: editItem.kategori || '',
          deskripsi: '',
          isReturnable: editItem.isReturnable ?? false,
          forSales: (editItem.salesPrice ?? 0) > 0 || true,
          forPurchases: (editItem.purchasePrice ?? 0) > 0 || true,
          salesAccount: 'account.sales',
          purchaseAccount: 'account.purchase',
          salesTax: '',
          purchaseTax: '',
          salesPrice: editItem.salesPrice ?? 0,
          purchasePrice: editItem.purchasePrice ?? 0,
          salesAccountId: editItemData.salesAccountId || undefined,
          purchaseAccountId: editItemData.purchaseAccountId || undefined,
          preferredVendorId: editItemData.preferredVendorId || undefined,
          reorderLevel: editItemData.reorderLevel || undefined,
          imageUrl: undefined,
          conversions: [],
        });
        if (editItemData.vendorName) {
          setVendorSearch(editItemData.vendorName);
        }
        // Set section visibility based on data
        setSalesActive((editItem.salesPrice ?? 0) > 0);
        setPurchaseActive((editItem.purchasePrice ?? 0) > 0);
      } else {
        // Create mode - reset to initial data
        setFormData(INITIAL_ITEM_DATA);
        setSalesActive(true);
        setPurchaseActive(true);
      }
      setError(null);
      setExpandedField(null);
      setUnitSearchQuery('');
      setCustomUnitValue('');
      setConversionUnitSheetIndex(-1);
      setConversionSheetOpen(false);
      setSalesInfoSheetOpen(false);
      setPurchaseInfoSheetOpen(false);
      setConversionQuantitySheetIndex(-1);
      setCustomQuantityValue('');
      setShowSuccessToast(false);
    }
  }, [isOpen, editItem]);

  // Reset unit search when sheet opens
  useEffect(() => {
    if (unitSheetOpen || conversionUnitSheetIndex >= 0) {
      setUnitSearchQuery('');
      setCustomUnitValue('');
    }
  }, [unitSheetOpen, conversionUnitSheetIndex]);

  // Fetch dropdown options from API
  useEffect(() => {
    const fetchDropdowns = async () => {
      try {
        const [catRes, taxRes, salesAccRes, purchAccRes] = await Promise.all([
          fetchWithAuth('/api/items/categories'),
          fetchWithAuth('/api/items/taxes'),
          fetchWithAuth('/api/items/accounts/sales'),
          fetchWithAuth('/api/items/accounts/purchase'),
        ]);

        if (catRes.ok) {
          const catData = await catRes.json();
          setCategories(catData.categories || []);
        }
        if (taxRes.ok) {
          const taxData = await taxRes.json();
          setTaxOptionsApi({
            goods: taxData.goods_taxes || [],
            services: taxData.service_taxes || [],
          });
        }
        if (salesAccRes.ok) {
          const accData = await salesAccRes.json();
          setSalesAccountsApi(accData.accounts || []);
        }
        if (purchAccRes.ok) {
          const accData = await purchAccRes.json();
          setPurchaseAccountsApi(accData.accounts || []);
        }
      } catch (err) {
        console.error('Failed to fetch dropdown options:', err);
      }
    };

    fetchDropdowns();
  }, []);

  // Vendor autocomplete
  useEffect(() => {
    if (!vendorSearch || vendorSearch.length < 2) {
      setVendorResults([]);
      return;
    }

    const timer = setTimeout(async () => {
      try {
        const res = await fetchWithAuth(`/api/vendors/autocomplete?search=${encodeURIComponent(vendorSearch)}&limit=10`);
        if (res.ok) {
          const data = await res.json();
          setVendorResults(data.data || []);
        }
      } catch (err) {
        console.error('Vendor search failed:', err);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [vendorSearch]);

  // Auto-add first conversion when sheet opens with no conversions
  useEffect(() => {
    if (conversionSheetOpen && formData.conversions.length === 0 && formData.baseUnit) {
      addConversion();
    }
  }, [conversionSheetOpen]);

  // Update form field
  const updateField = <K extends keyof CreateItemData>(key: K, value: CreateItemData[K]) => {
    setFormData(prev => ({ ...prev, [key]: value }));
    setError(null);
  };

  // Handle type change
  const handleTypeChange = (type: 'goods' | 'service') => {
    updateField('itemType', type);
    if (type === 'service') {
      updateField('trackInventory', false);
      updateField('isReturnable', false);
      updateField('conversions', []);
    }
  };

  // Add conversion
  const addConversion = () => {
    if (!formData.baseUnit) return;
    setFormData(prev => ({
      ...prev,
      conversions: [
        ...prev.conversions,
        { conversionUnit: '', conversionFactor: 1, purchasePrice: undefined, salesPrice: undefined },
      ],
    }));
  };

  // Update conversion
  const updateConversion = (index: number, updates: Partial<UnitConversionInput>) => {
    setFormData(prev => ({
      ...prev,
      conversions: prev.conversions.map((c, i) => i === index ? { ...c, ...updates } : c),
    }));
  };

  // Remove conversion
  const removeConversion = (index: number) => {
    setFormData(prev => ({
      ...prev,
      conversions: prev.conversions.filter((_, i) => i !== index),
    }));
  };

  // Create new category
  const handleCreateCategory = async () => {
    if (!newCategory.trim()) return;
    try {
      const res = await fetchWithAuth('/api/items/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newCategory.trim() }),
      });
      if (res.ok) {
        setCategories(prev => [...prev, newCategory.trim()]);
        setFormData(prev => ({ ...prev, kategori: newCategory.trim() }));
        setNewCategory('');
        setShowNewCategoryInput(false);
      }
    } catch (err) {
      console.error('Failed to create category:', err);
    }
  };

  // Submit form
  const handleSubmit = async () => {
    if (!formData.name.trim()) {
      setError('Nama item wajib diisi');
      return;
    }
    if (!formData.baseUnit.trim()) {
      setError('Satuan wajib dipilih');
      return;
    }
    // Zoho Books requires at least one of forSales or forPurchases
    if (!formData.forSales && !formData.forPurchases) {
      setError('Pilih minimal satu: Informasi Penjualan atau Informasi Pembelian');
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      const payload = {
        name: formData.name.trim(),
        item_type: formData.itemType,
        track_inventory: formData.trackInventory,
        base_unit: formData.baseUnit,
        barcode: formData.barcode || null,
        kategori: formData.kategori || null,
        deskripsi: formData.deskripsi || null,
        is_returnable: formData.isReturnable,
        for_sales: formData.forSales,
        for_purchases: formData.forPurchases,
        sales_account: formData.forSales ? formData.salesAccount : null,
        purchase_account: formData.forPurchases ? formData.purchaseAccount : null,
        sales_tax: formData.forSales ? (formData.salesTax || null) : null,
        purchase_tax: formData.forPurchases ? (formData.purchaseTax || null) : null,
        sales_price: formData.forSales ? (formData.salesPrice ?? null) : null,
        purchase_price: formData.forPurchases ? (formData.purchasePrice ?? null) : null,
        sales_account_id: formData.salesAccountId || undefined,
        purchase_account_id: formData.purchaseAccountId || undefined,
        preferred_vendor_id: formData.preferredVendorId || undefined,
        reorder_level: formData.reorderLevel || undefined,
        image_url: formData.imageUrl || undefined,
        conversions: formData.conversions.filter(c => c.conversionUnit).map(c => ({
          conversion_unit: c.conversionUnit,
          conversion_factor: c.conversionFactor,
          purchase_price: formData.forPurchases ? (c.purchasePrice ?? null) : null,
          sales_price: formData.forSales ? (c.salesPrice ?? null) : null,
        })),
      };

      // Use PUT for edit, POST for create
      const url = isEditMode ? `/api/items/${editItem?.id}` : '/api/items';
      const method = isEditMode ? 'PUT' : 'POST';

      const response = await fetchWithAuth(url, {
        method,
        body: JSON.stringify(payload),
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(extractErrorMessage(result, isEditMode ? 'Gagal mengubah item' : 'Gagal menyimpan item'));
      }

      // Show success toast
      setShowSuccessToast(true);
      setIsSubmitting(false);

      // After 1.5 seconds, hide toast and close form
      setTimeout(() => {
        setShowSuccessToast(false);
        onSuccess(result.data);
        onClose();
      }, 1500);
    } catch (err: any) {
      setError(err.message || 'Terjadi kesalahan');
      setIsSubmitting(false);
    }
  };

  // Get all units (base + conversions) for price rows
  const getAllUnits = () => {
    const units = [formData.baseUnit];
    formData.conversions.forEach(c => {
      if (c.conversionUnit) units.push(c.conversionUnit);
    });
    return units.filter(Boolean);
  };

  const taxOptions = formData.itemType === 'service'
    ? (taxOptionsApi.services.length > 0 ? taxOptionsApi.services : SERVICE_TAX_OPTIONS)
    : (taxOptionsApi.goods.length > 0 ? taxOptionsApi.goods : TAX_OPTIONS);

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ x: '100%' }}
          animate={{ x: 0 }}
          exit={{ x: '100%' }}
          transition={SPRING_CONFIG}
          style={{
            position: 'fixed',
            top: 0, left: 0, right: 0, bottom: 0,
            background: COLORS.bgPrimary,
            zIndex: 100,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {/* Header */}
          <header style={{
            padding: '16px 20px',
            position: 'sticky',
            top: 0,
            background: COLORS.bgPrimary,
            zIndex: 50,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderBottom: `1px solid ${COLORS.dividerColor}`,
          }}>
            <button
              onClick={onClose}
              style={{
                width: '40px', height: '40px', background: 'none', border: 'none',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', borderRadius: '12px',
              }}
            >
              <BackIcon />
            </button>
            <span style={{ fontSize: '15px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              {isEditMode ? 'Edit Item' : 'Tambah Item'}
            </span>
            <button
              onClick={handleSubmit}
              disabled={isSubmitting}
              style={{
                background: COLORS.bgCard,
                border: 'none',
                fontFamily: 'inherit',
                fontSize: '13px',
                fontWeight: 700,
                color: COLORS.textSecondary,
                padding: '8px 14px',
                borderRadius: '100px',
                cursor: 'pointer',
                opacity: isSubmitting ? 0.5 : 1,
              }}
            >
              {isSubmitting ? 'Menyimpan...' : 'Simpan'}
            </button>
          </header>

          {/* Content */}
          <main style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 40px' }}>
            {error && (
              <div style={{
                padding: '12px 16px',
                background: '#FEF2F2',
                borderRadius: '12px',
                color: '#EF4444',
                fontSize: '13px',
                marginBottom: '16px',
              }}>
                {error}
              </div>
            )}

            <SectionLabel>Info Dasar</SectionLabel>

            {/* Type Pills - BARANG, SCAN, JASA in one row */}
            <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
              <TypePill
                icon={<BoxIcon />}
                label="Barang"
                active={formData.itemType === 'goods'}
                onClick={() => handleTypeChange('goods')}
              />
              <TypePill
                icon={<ScanIcon />}
                active={!!formData.barcode}
                onClick={() => {
                  // TODO: Open barcode scanner
                  updateField('barcode', `SCAN-${Date.now()}`);
                }}
              />
              <TypePill
                icon={<ToolIcon />}
                label="Jasa"
                active={formData.itemType === 'service'}
                onClick={() => handleTypeChange('service')}
              />
            </div>

            {/* Name Field - FieldPill accordion pattern */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
              {/* Header Row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                {/* Pill Button */}
                <button
                  onClick={() => toggleField('name')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '12px 16px',
                    borderRadius: '100px',
                    cursor: 'pointer',
                    border: 'none',
                    fontFamily: 'inherit',
                    background: formData.name ? COLORS.bgCardDarker : COLORS.bgCard,
                  }}
                >
                  <BoxIcon />
                  <span style={{
                    fontSize: '13px',
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.3px',
                    color: COLORS.textPrimary,
                  }}>
                    Nama Item
                  </span>
                  <span style={{
                    transition: 'transform 0.2s ease',
                    transform: expandedField === 'name' ? 'rotate(180deg)' : 'rotate(0deg)',
                    display: 'flex',
                  }}>
                    <ChevronDownIcon />
                  </span>
                </button>
                {/* Action Button - visible when not expanded */}
                {expandedField !== 'name' && (
                  <button
                    onClick={() => {
                      setExpandedField('name');
                      setNameSheetOpen(true);
                    }}
                    style={{
                      width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                      background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0, marginRight: '8px',
                    }}
                  >
                    <PlusIcon />
                  </button>
                )}
              </div>
              {/* Expanded Input Field */}
              {expandedField === 'name' && (
                <div
                  onClick={() => setNameSheetOpen(true)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 8px 8px 16px',
                    border: formData.name ? `2px solid ${COLORS.borderColor}` : `2px dashed #C5C5C5`,
                    borderRadius: '100px',
                    background: COLORS.bgPrimary,
                    cursor: 'pointer',
                  }}
                >
                  <span style={{
                    flex: 1,
                    fontSize: '15px',
                    fontWeight: formData.name ? 600 : 500,
                    color: formData.name ? COLORS.textPrimary : COLORS.textMuted,
                  }}>
                    {formData.name || 'Ketuk untuk mengisi...'}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (formData.name) {
                        updateField('name', '');
                      } else {
                        setNameSheetOpen(true);
                      }
                    }}
                    style={{
                      width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                      background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0,
                    }}
                  >
                    {formData.name ? <CloseIcon /> : <PlusIcon />}
                  </button>
                </div>
              )}
            </div>

            {/* Unit Field (goods only) - FieldPill accordion pattern */}
            {formData.itemType === 'goods' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
                {/* Header Row */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  {/* Pill Button */}
                  <button
                    onClick={() => toggleField('unit')}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '12px 16px',
                      borderRadius: '100px',
                      cursor: 'pointer',
                      border: 'none',
                      fontFamily: 'inherit',
                      background: formData.baseUnit ? COLORS.bgCardDarker : COLORS.bgCard,
                    }}
                  >
                    <UnitIcon />
                    <span style={{
                      fontSize: '13px',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                      color: COLORS.textPrimary,
                    }}>
                      Satuan Beli
                    </span>
                    <span style={{
                      transition: 'transform 0.2s ease',
                      transform: expandedField === 'unit' ? 'rotate(180deg)' : 'rotate(0deg)',
                      display: 'flex',
                    }}>
                      <ChevronDownIcon />
                    </span>
                  </button>
                  {/* Action Button - visible when not expanded */}
                  {expandedField !== 'unit' && (
                    <button
                      onClick={() => {
                        setExpandedField('unit');
                        setUnitSheetOpen(true);
                      }}
                      style={{
                        width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                        background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                        cursor: 'pointer', flexShrink: 0, marginRight: '8px',
                      }}
                    >
                      <PlusIcon />
                    </button>
                  )}
                </div>
                {/* Expanded Input Field */}
                {expandedField === 'unit' && (
                  <div
                    onClick={() => setUnitSheetOpen(true)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '8px 8px 8px 16px',
                      border: formData.baseUnit ? `2px solid ${COLORS.borderColor}` : `2px dashed #C5C5C5`,
                      borderRadius: '100px',
                      background: COLORS.bgPrimary,
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{
                      flex: 1,
                      fontSize: '15px',
                      fontWeight: formData.baseUnit ? 600 : 500,
                      color: formData.baseUnit ? COLORS.textPrimary : COLORS.textMuted,
                    }}>
                      {formData.baseUnit || 'Ketuk untuk memilih...'}
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (formData.baseUnit) {
                          updateField('baseUnit', '');
                        } else {
                          setUnitSheetOpen(true);
                        }
                      }}
                      style={{
                        width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                        background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                        cursor: 'pointer', flexShrink: 0,
                      }}
                    >
                      {formData.baseUnit ? <CloseIcon /> : <PlusIcon />}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Unit Conversions (goods only) - Pill Field Pattern */}
            {formData.itemType === 'goods' && formData.baseUnit && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
                {/* Header Row */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  {/* Pill Button */}
                  <button
                    onClick={() => toggleField('conversion')}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '12px 16px',
                      borderRadius: '100px',
                      cursor: 'pointer',
                      border: 'none',
                      fontFamily: 'inherit',
                      background: formData.conversions.length > 0 ? COLORS.bgCardDarker : COLORS.bgCard,
                    }}
                  >
                    <ConversionIcon />
                    <span style={{
                      fontSize: '13px',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                      color: COLORS.textPrimary,
                    }}>
                      Konversi Satuan
                    </span>
                    <span style={{
                      transition: 'transform 0.2s ease',
                      transform: expandedField === 'conversion' ? 'rotate(180deg)' : 'rotate(0deg)',
                      display: 'flex',
                    }}>
                      <ChevronDownIcon />
                    </span>
                  </button>
                  {/* Action Button - visible when not expanded */}
                  {expandedField !== 'conversion' && (
                    <button
                      onClick={() => {
                        setExpandedField('conversion');
                        setConversionSheetOpen(true);
                      }}
                      style={{
                        width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                        background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                        cursor: 'pointer', flexShrink: 0, marginRight: '8px',
                      }}
                    >
                      <PlusIcon />
                    </button>
                  )}
                </div>
                {/* Expanded Field */}
                {expandedField === 'conversion' && (
                  <div
                    onClick={() => setConversionSheetOpen(true)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '8px 8px 8px 16px',
                      border: formData.conversions.length > 0 ? `2px solid ${COLORS.borderColor}` : `2px dashed #C5C5C5`,
                      borderRadius: '100px',
                      background: COLORS.bgPrimary,
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{
                      flex: 1,
                      fontSize: '15px',
                      fontWeight: formData.conversions.length > 0 ? 600 : 500,
                      color: formData.conversions.length > 0 ? COLORS.textPrimary : COLORS.textMuted,
                    }}>
                      {formData.conversions.length > 0
                        ? (() => {
                            // Show summary: 1 Box = 25 strip = 250 tablet
                            let total = 1;
                            const parts = [`1 ${formData.baseUnit}`];
                            formData.conversions.forEach((conv) => {
                              if (conv.conversionUnit) {
                                total *= conv.conversionFactor;
                                parts.push(`${total} ${conv.conversionUnit}`);
                              }
                            });
                            return parts.join(' → ');
                          })()
                        : 'Ketuk untuk menambah konversi...'}
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (formData.conversions.length > 0) {
                          updateField('conversions', []);
                        } else {
                          setConversionSheetOpen(true);
                        }
                      }}
                      style={{
                        width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                        background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                        cursor: 'pointer', flexShrink: 0,
                      }}
                    >
                      {formData.conversions.length > 0 ? <CloseIcon /> : <PlusIcon />}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Track Inventory - Pill with Toggle */}
            {formData.itemType === 'goods' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <button
                    onClick={() => updateField('trackInventory', !formData.trackInventory)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '12px 16px',
                      borderRadius: '100px',
                      cursor: 'pointer',
                      border: 'none',
                      fontFamily: 'inherit',
                      background: formData.trackInventory ? COLORS.bgCardDarker : COLORS.bgCard,
                    }}
                  >
                    <BoxIcon />
                    <span style={{
                      fontSize: '13px',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                      color: COLORS.textPrimary,
                    }}>
                      Track Inventory
                    </span>
                  </button>
                  <div style={{ marginRight: '8px' }}>
                    <ToggleSwitch value={formData.trackInventory} onChange={(v) => updateField('trackInventory', v)} />
                  </div>
                </div>
              </div>
            )}

            {/* Returnable - Pill with Toggle */}
            {formData.itemType === 'goods' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <button
                    onClick={() => updateField('isReturnable', !formData.isReturnable)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '12px 16px',
                      borderRadius: '100px',
                      cursor: 'pointer',
                      border: 'none',
                      fontFamily: 'inherit',
                      background: formData.isReturnable ? COLORS.bgCardDarker : COLORS.bgCard,
                    }}
                  >
                    <svg style={{ width: '18px', height: '18px', color: COLORS.textSecondary }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3" />
                    </svg>
                    <span style={{
                      fontSize: '13px',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                      color: COLORS.textPrimary,
                    }}>
                      Bisa Retur
                    </span>
                  </button>
                  <div style={{ marginRight: '8px' }}>
                    <ToggleSwitch value={formData.isReturnable} onChange={(v) => updateField('isReturnable', v)} />
                  </div>
                </div>
              </div>
            )}

            {/* Kategori */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 14, fontWeight: 500, color: COLORS.textSecondary, marginBottom: 6 }}>
                Kategori
              </label>
              <div style={{ display: 'flex', gap: 8 }}>
                <select
                  value={formData.kategori || ''}
                  onChange={(e) => setFormData(prev => ({ ...prev, kategori: e.target.value }))}
                  style={{
                    flex: 1, padding: '12px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                    borderRadius: 12, fontSize: 15, fontFamily: 'inherit', color: COLORS.textPrimary,
                    appearance: 'none' as any, WebkitAppearance: 'none' as any,
                  }}
                >
                  <option value="">Pilih kategori</option>
                  {categories.map(cat => (
                    <option key={cat} value={cat}>{cat}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setShowNewCategoryInput(true)}
                  style={{
                    width: 44, height: 44, background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                    borderRadius: 12, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  <PlusIcon />
                </button>
              </div>
              {showNewCategoryInput && (
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <input
                    value={newCategory}
                    onChange={(e) => setNewCategory(e.target.value)}
                    placeholder="Nama kategori baru"
                    style={{
                      flex: 1, padding: '10px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                      borderRadius: 12, fontSize: 14, fontFamily: 'inherit',
                    }}
                  />
                  <button
                    type="button"
                    onClick={handleCreateCategory}
                    style={{
                      padding: '10px 16px', background: COLORS.accentOlive, color: '#FFF',
                      border: 'none', borderRadius: 12, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
                    }}
                  >
                    Tambah
                  </button>
                </div>
              )}
            </div>

            {/* Deskripsi */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 14, fontWeight: 500, color: COLORS.textSecondary, marginBottom: 6 }}>
                Deskripsi
              </label>
              <textarea
                value={formData.deskripsi || ''}
                onChange={(e) => setFormData(prev => ({ ...prev, deskripsi: e.target.value }))}
                placeholder="Deskripsi untuk tampil di invoice (opsional)"
                rows={3}
                style={{
                  width: '100%', padding: '12px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                  borderRadius: 12, fontSize: 15, fontFamily: 'inherit', color: COLORS.textPrimary, resize: 'vertical',
                  boxSizing: 'border-box' as any,
                }}
              />
            </div>

            <div style={{ height: '1px', background: COLORS.dividerColor, margin: '16px 0' }} />

            {/* Sales Info - Pill Field Pattern */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
              {/* Header Row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                {/* Pill Button */}
                <button
                  onClick={() => toggleField('sales')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '12px 16px',
                    borderRadius: '100px',
                    cursor: 'pointer',
                    border: 'none',
                    fontFamily: 'inherit',
                    background: (formData.salesPrice ?? 0) > 0 ? COLORS.bgCardDarker : COLORS.bgCard,
                  }}
                >
                  <CurrencyIcon />
                  <span style={{
                    fontSize: '13px',
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.3px',
                    color: COLORS.textPrimary,
                  }}>
                    Info Penjualan
                  </span>
                  <span style={{
                    transition: 'transform 0.2s ease',
                    transform: expandedField === 'sales' ? 'rotate(180deg)' : 'rotate(0deg)',
                    display: 'flex',
                  }}>
                    <ChevronDownIcon />
                  </span>
                </button>
                {/* Action Button */}
                {expandedField !== 'sales' && (
                  <button
                    onClick={() => {
                      setExpandedField('sales');
                      setSalesInfoSheetOpen(true);
                    }}
                    style={{
                      width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                      background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0, marginRight: '8px',
                    }}
                  >
                    <PlusIcon />
                  </button>
                )}
              </div>
              {/* Expanded Field */}
              {expandedField === 'sales' && (
                <div
                  onClick={() => setSalesInfoSheetOpen(true)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 8px 8px 16px',
                    border: (formData.salesPrice ?? 0) > 0 ? `2px solid ${COLORS.borderColor}` : `2px dashed #C5C5C5`,
                    borderRadius: '100px',
                    background: COLORS.bgPrimary,
                    cursor: 'pointer',
                  }}
                >
                  <span style={{
                    flex: 1,
                    fontSize: '15px',
                    fontWeight: (formData.salesPrice ?? 0) > 0 ? 600 : 500,
                    color: (formData.salesPrice ?? 0) > 0 ? COLORS.textPrimary : COLORS.textMuted,
                  }}>
                    {(formData.salesPrice ?? 0) > 0
                      ? `Rp ${(formData.salesPrice ?? 0).toLocaleString('id-ID')}/${formData.baseUnit || 'unit'}`
                      : 'Ketuk untuk mengisi...'}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setSalesInfoSheetOpen(true);
                    }}
                    style={{
                      width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                      background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0,
                    }}
                  >
                    <ChevronIcon />
                  </button>
                </div>
              )}
            </div>

            {/* Purchase Info - Pill Field Pattern */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
              {/* Header Row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                {/* Pill Button */}
                <button
                  onClick={() => toggleField('purchase')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '12px 16px',
                    borderRadius: '100px',
                    cursor: 'pointer',
                    border: 'none',
                    fontFamily: 'inherit',
                    background: (formData.purchasePrice ?? 0) > 0 ? COLORS.bgCardDarker : COLORS.bgCard,
                  }}
                >
                  <CartIcon />
                  <span style={{
                    fontSize: '13px',
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.3px',
                    color: COLORS.textPrimary,
                  }}>
                    Info Pembelian
                  </span>
                  <span style={{
                    transition: 'transform 0.2s ease',
                    transform: expandedField === 'purchase' ? 'rotate(180deg)' : 'rotate(0deg)',
                    display: 'flex',
                  }}>
                    <ChevronDownIcon />
                  </span>
                </button>
                {/* Action Button */}
                {expandedField !== 'purchase' && (
                  <button
                    onClick={() => {
                      setExpandedField('purchase');
                      setPurchaseInfoSheetOpen(true);
                    }}
                    style={{
                      width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                      background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0, marginRight: '8px',
                    }}
                  >
                    <PlusIcon />
                  </button>
                )}
              </div>
              {/* Expanded Field */}
              {expandedField === 'purchase' && (
                <div
                  onClick={() => setPurchaseInfoSheetOpen(true)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 8px 8px 16px',
                    border: (formData.purchasePrice ?? 0) > 0 ? `2px solid ${COLORS.borderColor}` : `2px dashed #C5C5C5`,
                    borderRadius: '100px',
                    background: COLORS.bgPrimary,
                    cursor: 'pointer',
                  }}
                >
                  <span style={{
                    flex: 1,
                    fontSize: '15px',
                    fontWeight: (formData.purchasePrice ?? 0) > 0 ? 600 : 500,
                    color: (formData.purchasePrice ?? 0) > 0 ? COLORS.textPrimary : COLORS.textMuted,
                  }}>
                    {(formData.purchasePrice ?? 0) > 0
                      ? `Rp ${(formData.purchasePrice ?? 0).toLocaleString('id-ID')}/${formData.baseUnit || 'unit'}`
                      : 'Ketuk untuk mengisi...'}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setPurchaseInfoSheetOpen(true);
                    }}
                    style={{
                      width: '32px', height: '32px', borderRadius: '50%', border: 'none',
                      background: COLORS.bgCard, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: 'pointer', flexShrink: 0,
                    }}
                  >
                    <ChevronIcon />
                  </button>
                </div>
              )}
            </div>

            {/* ======== AKUN & VENDOR ======== */}
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: COLORS.textMuted, marginBottom: 12, paddingTop: 8 }}>
                Akun & Vendor
              </div>

              {/* Akun Pendapatan */}
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 14, fontWeight: 500, color: COLORS.textSecondary, marginBottom: 6 }}>
                  Akun Pendapatan
                </label>
                <select
                  value={formData.salesAccountId || formData.salesAccount || ''}
                  onChange={(e) => {
                    const selected = salesAccountsApi.find(a => a.id === e.target.value);
                    if (selected) {
                      setFormData(prev => ({ ...prev, salesAccount: selected.name, salesAccountId: selected.id }));
                    } else {
                      setFormData(prev => ({ ...prev, salesAccount: e.target.value, salesAccountId: undefined }));
                    }
                  }}
                  style={{
                    width: '100%', padding: '12px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                    borderRadius: 12, fontSize: 15, fontFamily: 'inherit', color: COLORS.textPrimary,
                    appearance: 'none' as any, WebkitAppearance: 'none' as any, boxSizing: 'border-box' as any,
                  }}
                >
                  <option value="">Pilih akun</option>
                  {(salesAccountsApi.length > 0
                    ? salesAccountsApi.map(acc => ({ id: acc.id, label: acc.code ? `${acc.code} - ${acc.name}` : acc.name }))
                    : SALES_ACCOUNTS.map(a => ({ id: a.value, label: a.label }))
                  ).map(acc => (
                    <option key={acc.id} value={acc.id}>{acc.label}</option>
                  ))}
                </select>
              </div>

              {/* Akun Beban */}
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 14, fontWeight: 500, color: COLORS.textSecondary, marginBottom: 6 }}>
                  Akun Beban
                </label>
                <select
                  value={formData.purchaseAccountId || formData.purchaseAccount || ''}
                  onChange={(e) => {
                    const selected = purchaseAccountsApi.find(a => a.id === e.target.value);
                    if (selected) {
                      setFormData(prev => ({ ...prev, purchaseAccount: selected.name, purchaseAccountId: selected.id }));
                    } else {
                      setFormData(prev => ({ ...prev, purchaseAccount: e.target.value, purchaseAccountId: undefined }));
                    }
                  }}
                  style={{
                    width: '100%', padding: '12px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                    borderRadius: 12, fontSize: 15, fontFamily: 'inherit', color: COLORS.textPrimary,
                    appearance: 'none' as any, WebkitAppearance: 'none' as any, boxSizing: 'border-box' as any,
                  }}
                >
                  <option value="">Pilih akun</option>
                  {(purchaseAccountsApi.length > 0
                    ? purchaseAccountsApi.map(acc => ({ id: acc.id, label: acc.code ? `${acc.code} - ${acc.name}` : acc.name }))
                    : PURCHASE_ACCOUNTS.map(a => ({ id: a.value, label: a.label }))
                  ).map(acc => (
                    <option key={acc.id} value={acc.id}>{acc.label}</option>
                  ))}
                </select>
              </div>

              {/* Vendor Utama */}
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 14, fontWeight: 500, color: COLORS.textSecondary, marginBottom: 6 }}>
                  Vendor Utama
                </label>
                <input
                  value={vendorSearch}
                  onChange={(e) => setVendorSearch(e.target.value)}
                  placeholder="Cari vendor..."
                  style={{
                    width: '100%', padding: '12px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                    borderRadius: 12, fontSize: 15, fontFamily: 'inherit', color: COLORS.textPrimary, boxSizing: 'border-box' as any,
                  }}
                />
                {vendorResults.length > 0 && (
                  <div style={{
                    marginTop: 4, background: '#FFF', border: `1px solid ${COLORS.borderColor}`,
                    borderRadius: 12, overflow: 'hidden', maxHeight: 200, overflowY: 'auto' as any,
                  }}>
                    {vendorResults.map(v => (
                      <button
                        key={v.id}
                        type="button"
                        onClick={() => {
                          setFormData(prev => ({ ...prev, preferredVendorId: v.id }));
                          setVendorSearch(v.name);
                          setVendorResults([]);
                        }}
                        style={{
                          width: '100%', padding: '10px 14px', background: 'none', border: 'none',
                          borderBottom: `1px solid ${COLORS.dividerColor}`, textAlign: 'left' as any,
                          fontSize: 14, fontFamily: 'inherit', cursor: 'pointer', color: COLORS.textPrimary,
                        }}
                      >
                        {v.name} {v.code && <span style={{ color: COLORS.textMuted }}>({v.code})</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* ======== INVENTORI (Titik Reorder) ======== */}
            {formData.itemType === 'goods' && formData.trackInventory && (
              <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: COLORS.textMuted, marginBottom: 12, paddingTop: 8 }}>
                  Inventori
                </div>

                {/* Titik Reorder */}
                <div style={{ marginBottom: 16 }}>
                  <label style={{ display: 'block', fontSize: 14, fontWeight: 500, color: COLORS.textSecondary, marginBottom: 6 }}>
                    Titik Reorder
                  </label>
                  <input
                    type="text"
                    value={formatNumber(formData.reorderLevel)}
                    onChange={(e) => {
                      const val = parseFormattedNumber(e.target.value);
                      setFormData(prev => ({ ...prev, reorderLevel: val || undefined }));
                    }}
                    placeholder="0"
                    style={{
                      width: '100%', padding: '12px 14px', background: COLORS.bgCard, border: `1px solid ${COLORS.borderColor}`,
                      borderRadius: 12, fontSize: 15, fontFamily: 'inherit', color: COLORS.textPrimary, boxSizing: 'border-box' as any,
                    }}
                  />
                  <div style={{ fontSize: 12, color: COLORS.textMuted, marginTop: 4 }}>
                    Notifikasi saat stok mencapai angka ini
                  </div>
                </div>
              </div>
            )}
          </main>

          {/* Bottom Sheets */}
          {/* Name Sheet */}
          <BottomSheet isOpen={nameSheetOpen} onClose={() => setNameSheetOpen(false)} title="Nama Item">
            <input
              ref={nameInputRef}
              type="text"
              value={formData.name}
              onChange={(e) => updateField('name', e.target.value)}
              placeholder="Masukkan nama item"
              autoFocus
              style={{
                width: '100%',
                padding: '14px 16px',
                background: COLORS.bgCard,
                border: 'none',
                borderRadius: '16px',
                fontFamily: 'inherit',
                fontSize: '15px',
                marginBottom: '12px',
                outline: 'none',
              }}
            />
            <button
              onClick={() => {
                setExpandedField('name');
                setNameSheetOpen(false);
              }}
              style={{
                width: '100%',
                padding: '14px',
                background: COLORS.bgCard,
                border: 'none',
                borderRadius: '16px',
                fontFamily: 'inherit',
                fontSize: '15px',
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              Simpan
            </button>
          </BottomSheet>

          {/* Unit Sheet with Search */}
          <BottomSheet isOpen={unitSheetOpen} onClose={() => setUnitSheetOpen(false)} title="Pilih Satuan">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {/* Search Bar - Sticky */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '12px 16px',
                background: COLORS.bgCard,
                borderRadius: '14px',
                position: 'sticky',
                top: 0,
                zIndex: 10,
              }}>
                <SearchIcon />
                <input
                  type="text"
                  value={unitSearchQuery}
                  onChange={(e) => setUnitSearchQuery(e.target.value)}
                  placeholder="Cari satuan..."
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    fontFamily: 'inherit',
                    fontSize: '15px',
                    fontWeight: 500,
                    color: COLORS.textPrimary,
                    outline: 'none',
                  }}
                />
                {unitSearchQuery && (
                  <button
                    onClick={() => setUnitSearchQuery('')}
                    style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
                  >
                    <CloseIcon />
                  </button>
                )}
              </div>

              {/* Scrollable Options */}
              <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
                {DEFAULT_UNITS
                  .filter(unit => unit.toLowerCase().includes(unitSearchQuery.toLowerCase()))
                  .map(unit => (
                    <OptionItem
                      key={unit}
                      label={unit}
                      selected={formData.baseUnit === unit}
                      onClick={() => {
                        updateField('baseUnit', unit);
                        setExpandedField('unit');
                        setUnitSheetOpen(false);
                      }}
                    />
                  ))}
              </div>

              {/* Divider */}
              <div style={{ height: '1px', background: COLORS.dividerColor }} />

              {/* Add Custom Unit - Sticky Bottom */}
              <div style={{ position: 'sticky', bottom: 0, background: COLORS.bgPrimary, paddingTop: '4px' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.3px', color: COLORS.textMuted, marginBottom: '8px' }}>
                  Tambah satuan baru
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <input
                    type="text"
                    value={customUnitValue}
                    onChange={(e) => setCustomUnitValue(e.target.value)}
                    placeholder="Ketik nama satuan..."
                    style={{
                      flex: 1,
                      padding: '14px 16px',
                      background: COLORS.bgCard,
                      borderRadius: '14px',
                      border: 'none',
                      fontFamily: 'inherit',
                      fontSize: '15px',
                      fontWeight: 500,
                      color: COLORS.textPrimary,
                      outline: 'none',
                    }}
                  />
                  <button
                    onClick={() => {
                      if (customUnitValue.trim()) {
                        updateField('baseUnit', customUnitValue.trim());
                        setExpandedField('unit');
                        setUnitSheetOpen(false);
                      }
                    }}
                    disabled={!customUnitValue.trim()}
                    style={{
                      padding: '14px 20px',
                      background: customUnitValue.trim() ? COLORS.accentOlive : COLORS.bgCard,
                      border: 'none',
                      borderRadius: '14px',
                      fontFamily: 'inherit',
                      fontSize: '14px',
                      fontWeight: 700,
                      color: customUnitValue.trim() ? '#FFFFFF' : COLORS.textMuted,
                      cursor: customUnitValue.trim() ? 'pointer' : 'not-allowed',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    Tambah
                  </button>
                </div>
              </div>
            </div>
          </BottomSheet>

          {/* Conversion Unit Sheet */}
          <BottomSheet
            isOpen={conversionUnitSheetIndex >= 0}
            onClose={() => {
              setConversionUnitSheetIndex(-1);
              setTimeout(() => setConversionSheetOpen(true), 200);
            }}
            title="Pilih Satuan Konversi"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {/* Search Bar */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '12px 16px',
                background: COLORS.bgCard,
                borderRadius: '14px',
                position: 'sticky',
                top: 0,
                zIndex: 10,
              }}>
                <SearchIcon />
                <input
                  type="text"
                  value={unitSearchQuery}
                  onChange={(e) => setUnitSearchQuery(e.target.value)}
                  placeholder="Cari satuan..."
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    fontFamily: 'inherit',
                    fontSize: '15px',
                    fontWeight: 500,
                    color: COLORS.textPrimary,
                    outline: 'none',
                  }}
                />
                {unitSearchQuery && (
                  <button
                    onClick={() => setUnitSearchQuery('')}
                    style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
                  >
                    <CloseIcon />
                  </button>
                )}
              </div>

              {/* Scrollable Options */}
              <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
                {DEFAULT_UNITS
                  .filter(unit => unit.toLowerCase().includes(unitSearchQuery.toLowerCase()))
                  .map(unit => (
                    <OptionItem
                      key={unit}
                      label={unit}
                      selected={conversionUnitSheetIndex >= 0 && formData.conversions[conversionUnitSheetIndex]?.conversionUnit === unit}
                      onClick={() => {
                        if (conversionUnitSheetIndex >= 0) {
                          updateConversion(conversionUnitSheetIndex, { conversionUnit: unit });
                          setConversionUnitSheetIndex(-1);
                          setTimeout(() => setConversionSheetOpen(true), 200);
                        }
                      }}
                    />
                  ))}
              </div>

              {/* Divider */}
              <div style={{ height: '1px', background: COLORS.dividerColor }} />

              {/* Add Custom Unit */}
              <div style={{ position: 'sticky', bottom: 0, background: COLORS.bgPrimary, paddingTop: '4px' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.3px', color: COLORS.textMuted, marginBottom: '8px' }}>
                  Tambah satuan baru
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <input
                    type="text"
                    value={customUnitValue}
                    onChange={(e) => setCustomUnitValue(e.target.value)}
                    placeholder="Ketik nama satuan..."
                    style={{
                      flex: 1,
                      padding: '14px 16px',
                      background: COLORS.bgCard,
                      borderRadius: '14px',
                      border: 'none',
                      fontFamily: 'inherit',
                      fontSize: '15px',
                      fontWeight: 500,
                      color: COLORS.textPrimary,
                      outline: 'none',
                    }}
                  />
                  <button
                    onClick={() => {
                      if (customUnitValue.trim() && conversionUnitSheetIndex >= 0) {
                        updateConversion(conversionUnitSheetIndex, { conversionUnit: customUnitValue.trim() });
                        setConversionUnitSheetIndex(-1);
                        setCustomUnitValue('');
                        setTimeout(() => setConversionSheetOpen(true), 200);
                      }
                    }}
                    disabled={!customUnitValue.trim()}
                    style={{
                      padding: '14px 20px',
                      background: customUnitValue.trim() ? COLORS.accentOlive : COLORS.bgCard,
                      border: 'none',
                      borderRadius: '14px',
                      fontFamily: 'inherit',
                      fontSize: '14px',
                      fontWeight: 700,
                      color: customUnitValue.trim() ? '#FFFFFF' : COLORS.textMuted,
                      cursor: customUnitValue.trim() ? 'pointer' : 'not-allowed',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    Tambah
                  </button>
                </div>
              </div>
            </div>
          </BottomSheet>

          {/* Sales Account Sheet */}
          <BottomSheet isOpen={salesAccountSheetOpen} onClose={() => setSalesAccountSheetOpen(false)} title="Akun Pendapatan">
            {SALES_ACCOUNTS.map(acc => (
              <OptionItem
                key={acc.value}
                label={acc.label}
                selected={formData.salesAccount === acc.value}
                onClick={() => {
                  updateField('salesAccount', acc.value);
                  setSalesAccountSheetOpen(false);
                }}
              />
            ))}
          </BottomSheet>

          {/* Purchase Account Sheet */}
          <BottomSheet isOpen={purchaseAccountSheetOpen} onClose={() => setPurchaseAccountSheetOpen(false)} title="Akun Beban">
            {PURCHASE_ACCOUNTS.map(acc => (
              <OptionItem
                key={acc.value}
                label={acc.label}
                selected={formData.purchaseAccount === acc.value}
                onClick={() => {
                  updateField('purchaseAccount', acc.value);
                  setPurchaseAccountSheetOpen(false);
                }}
              />
            ))}
          </BottomSheet>

          {/* Sales Tax Sheet */}
          <BottomSheet isOpen={salesTaxSheetOpen} onClose={() => setSalesTaxSheetOpen(false)} title="Pajak Penjualan">
            {taxOptions.map(tax => (
              <OptionItem
                key={tax.value}
                label={tax.label}
                selected={formData.salesTax === tax.value}
                onClick={() => {
                  updateField('salesTax', tax.value);
                  setSalesTaxSheetOpen(false);
                }}
              />
            ))}
          </BottomSheet>

          {/* Purchase Tax Sheet */}
          <BottomSheet isOpen={purchaseTaxSheetOpen} onClose={() => setPurchaseTaxSheetOpen(false)} title="Pajak Pembelian">
            {taxOptions.map(tax => (
              <OptionItem
                key={tax.value}
                label={tax.label}
                selected={formData.purchaseTax === tax.value}
                onClick={() => {
                  updateField('purchaseTax', tax.value);
                  setPurchaseTaxSheetOpen(false);
                }}
              />
            ))}
          </BottomSheet>

          {/* Conversion Sheet */}
          <BottomSheet isOpen={conversionSheetOpen} onClose={() => setConversionSheetOpen(false)} title="Konversi Satuan">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {/* Conversion Rows - No wrapper, flat layout */}
              {formData.conversions.map((conv, idx) => {
                const sourceUnit = idx === 0
                  ? formData.baseUnit
                  : formData.conversions[idx - 1]?.conversionUnit || formData.baseUnit;

                return (
                  <div key={idx} style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                  }}>
                    {/* Source unit pill */}
                    <span style={{
                      padding: '10px 14px',
                      background: COLORS.bgCard,
                      borderRadius: '100px',
                      fontSize: '14px',
                      fontWeight: 600,
                      color: COLORS.textSecondary,
                      whiteSpace: 'nowrap',
                    }}>
                      1 {sourceUnit}
                    </span>
                    <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.textMuted }}>=</span>
                    {/* Quantity picker pill */}
                    <div
                      onClick={() => {
                        setConversionSheetOpen(false);
                        setTimeout(() => setConversionQuantitySheetIndex(idx), 200);
                      }}
                      style={{
                        minWidth: '70px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '4px',
                        padding: '10px 10px 10px 14px',
                        background: COLORS.bgCard,
                        borderRadius: '100px',
                        cursor: 'pointer',
                      }}
                    >
                      <span style={{
                        fontSize: '14px',
                        fontWeight: 700,
                        color: COLORS.textPrimary,
                      }}>
                        {conv.conversionFactor}
                      </span>
                      <ChevronIcon />
                    </div>
                    {/* Unit picker pill */}
                    <div
                      onClick={() => {
                        setConversionSheetOpen(false);
                        setTimeout(() => setConversionUnitSheetIndex(idx), 200);
                      }}
                      style={{
                        minWidth: '70px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '4px',
                        padding: '10px 10px 10px 14px',
                        background: conv.conversionUnit ? COLORS.bgCard : COLORS.bgPrimary,
                        border: conv.conversionUnit ? 'none' : `1px dashed ${COLORS.borderColor}`,
                        borderRadius: '100px',
                        cursor: 'pointer',
                      }}
                    >
                      <span style={{
                        fontSize: '14px',
                        fontWeight: conv.conversionUnit ? 600 : 500,
                        color: conv.conversionUnit ? COLORS.textPrimary : COLORS.textMuted,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        maxWidth: '80px',
                      }}>
                        {conv.conversionUnit || 'Pilih'}
                      </span>
                      <ChevronIcon />
                    </div>
                    {/* Remove */}
                    <button
                      onClick={() => removeConversion(idx)}
                      style={{
                        width: '32px', height: '32px', border: 'none',
                        background: COLORS.bgCard, borderRadius: '50%',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        cursor: 'pointer', flexShrink: 0,
                      }}
                    >
                      <CloseIcon />
                    </button>
                  </div>
                );
              })}

              {/* Add button */}
              <button
                onClick={addConversion}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px',
                  padding: '14px',
                  border: `1px dashed ${COLORS.borderColor}`,
                  borderRadius: '14px',
                  background: 'none',
                  fontFamily: 'inherit',
                  fontSize: '14px',
                  fontWeight: 600,
                  color: COLORS.textMuted,
                  cursor: 'pointer',
                }}
              >
                <PlusIcon />
                Tambah Konversi
              </button>

              {/* Summary */}
              {formData.conversions.length > 0 && formData.conversions.every(c => c.conversionUnit) && (
                <div style={{
                  padding: '14px',
                  background: COLORS.accentOliveLight,
                  borderRadius: '12px',
                  textAlign: 'center',
                }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.accentOlive }}>
                    1 {formData.baseUnit} = {(() => {
                      let total = 1;
                      const parts: string[] = [];
                      formData.conversions.forEach((conv) => {
                        total *= conv.conversionFactor;
                        parts.push(`${total} ${conv.conversionUnit}`);
                      });
                      return parts.join(' = ');
                    })()}
                  </span>
                </div>
              )}

              {/* Done button */}
              <button
                onClick={() => {
                  setExpandedField('conversion');
                  setConversionSheetOpen(false);
                }}
                style={{
                  width: '100%',
                  padding: '14px',
                  background: COLORS.accentOlive,
                  border: 'none',
                  borderRadius: '14px',
                  fontFamily: 'inherit',
                  fontSize: '15px',
                  fontWeight: 700,
                  color: '#FFFFFF',
                  cursor: 'pointer',
                }}
              >
                Selesai
              </button>
            </div>
          </BottomSheet>

          {/* Sales Info Sheet */}
          <BottomSheet isOpen={salesInfoSheetOpen} onClose={() => setSalesInfoSheetOpen(false)} title="Info Penjualan">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {/* Price for base unit */}
              <div>
                <div style={{ fontSize: '12px', fontWeight: 600, color: COLORS.textMuted, marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                  Harga Jual ({formData.baseUnit || 'unit'})
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '14px', background: COLORS.bgCard, borderRadius: '14px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.textSecondary }}>Rp</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={formatNumber(formData.salesPrice)}
                    onChange={(e) => updateField('salesPrice', parseFormattedNumber(e.target.value))}
                    placeholder="0"
                    style={{
                      flex: 1,
                      background: 'transparent',
                      border: 'none',
                      fontFamily: 'inherit',
                      fontSize: '16px',
                      fontWeight: 600,
                      color: COLORS.textPrimary,
                      outline: 'none',
                    }}
                  />
                </div>
              </div>

              {/* Prices for conversion units */}
              {formData.conversions.filter(c => c.conversionUnit).map((conv, idx) => (
                <div key={idx}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: COLORS.textMuted, marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                    Harga Jual ({conv.conversionUnit})
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '14px', background: COLORS.bgCard, borderRadius: '14px' }}>
                    <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.textSecondary }}>Rp</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={formatNumber(conv.salesPrice)}
                      onChange={(e) => updateConversion(idx, { salesPrice: parseFormattedNumber(e.target.value) })}
                      placeholder="0"
                      style={{
                        flex: 1,
                        background: 'transparent',
                        border: 'none',
                        fontFamily: 'inherit',
                        fontSize: '16px',
                        fontWeight: 600,
                        color: COLORS.textPrimary,
                        outline: 'none',
                      }}
                    />
                  </div>
                </div>
              ))}

              {/* Account & Tax */}
              <div
                onClick={() => {
                  setSalesInfoSheetOpen(false);
                  setTimeout(() => setSalesAccountSheetOpen(true), 200);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '14px',
                  background: COLORS.bgCard,
                  borderRadius: '14px',
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>Akun Pendapatan</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: formData.salesAccount ? COLORS.textPrimary : COLORS.textMuted }}>
                    {formData.salesAccount || 'Pilih'}
                  </span>
                  <ChevronIcon />
                </div>
              </div>

              <div
                onClick={() => {
                  setSalesInfoSheetOpen(false);
                  setTimeout(() => setSalesTaxSheetOpen(true), 200);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '14px',
                  background: COLORS.bgCard,
                  borderRadius: '14px',
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>Pajak</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: formData.salesTax ? COLORS.textPrimary : COLORS.textMuted }}>
                    {taxOptions.find(t => t.value === formData.salesTax)?.label || 'Pilih'}
                  </span>
                  <ChevronIcon />
                </div>
              </div>

              {/* Done button */}
              <button
                onClick={() => {
                  setExpandedField('sales');
                  setSalesInfoSheetOpen(false);
                }}
                style={{
                  width: '100%',
                  padding: '14px',
                  background: COLORS.accentOlive,
                  border: 'none',
                  borderRadius: '14px',
                  fontFamily: 'inherit',
                  fontSize: '15px',
                  fontWeight: 700,
                  color: '#FFFFFF',
                  cursor: 'pointer',
                }}
              >
                Selesai
              </button>
            </div>
          </BottomSheet>

          {/* Purchase Info Sheet */}
          <BottomSheet isOpen={purchaseInfoSheetOpen} onClose={() => setPurchaseInfoSheetOpen(false)} title="Info Pembelian">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {/* Price for base unit */}
              <div>
                <div style={{ fontSize: '12px', fontWeight: 600, color: COLORS.textMuted, marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                  Harga Beli ({formData.baseUnit || 'unit'})
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '14px', background: COLORS.bgCard, borderRadius: '14px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.textSecondary }}>Rp</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={formatNumber(formData.purchasePrice)}
                    onChange={(e) => updateField('purchasePrice', parseFormattedNumber(e.target.value))}
                    placeholder="0"
                    style={{
                      flex: 1,
                      background: 'transparent',
                      border: 'none',
                      fontFamily: 'inherit',
                      fontSize: '16px',
                      fontWeight: 600,
                      color: COLORS.textPrimary,
                      outline: 'none',
                    }}
                  />
                </div>
              </div>

              {/* Prices for conversion units */}
              {formData.conversions.filter(c => c.conversionUnit).map((conv, idx) => (
                <div key={idx}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: COLORS.textMuted, marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                    Harga Beli ({conv.conversionUnit})
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '14px', background: COLORS.bgCard, borderRadius: '14px' }}>
                    <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.textSecondary }}>Rp</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={formatNumber(conv.purchasePrice)}
                      onChange={(e) => updateConversion(idx, { purchasePrice: parseFormattedNumber(e.target.value) })}
                      placeholder="0"
                      style={{
                        flex: 1,
                        background: 'transparent',
                        border: 'none',
                        fontFamily: 'inherit',
                        fontSize: '16px',
                        fontWeight: 600,
                        color: COLORS.textPrimary,
                        outline: 'none',
                      }}
                    />
                  </div>
                </div>
              ))}

              {/* Account & Tax */}
              <div
                onClick={() => {
                  setPurchaseInfoSheetOpen(false);
                  setTimeout(() => setPurchaseAccountSheetOpen(true), 200);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '14px',
                  background: COLORS.bgCard,
                  borderRadius: '14px',
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>Akun Beban</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: formData.purchaseAccount ? COLORS.textPrimary : COLORS.textMuted }}>
                    {formData.purchaseAccount || 'Pilih'}
                  </span>
                  <ChevronIcon />
                </div>
              </div>

              <div
                onClick={() => {
                  setPurchaseInfoSheetOpen(false);
                  setTimeout(() => setPurchaseTaxSheetOpen(true), 200);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '14px',
                  background: COLORS.bgCard,
                  borderRadius: '14px',
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: '14px', color: COLORS.textSecondary }}>Pajak</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: formData.purchaseTax ? COLORS.textPrimary : COLORS.textMuted }}>
                    {taxOptions.find(t => t.value === formData.purchaseTax)?.label || 'Pilih'}
                  </span>
                  <ChevronIcon />
                </div>
              </div>

              {/* Done button */}
              <button
                onClick={() => {
                  setExpandedField('purchase');
                  setPurchaseInfoSheetOpen(false);
                }}
                style={{
                  width: '100%',
                  padding: '14px',
                  background: COLORS.accentOlive,
                  border: 'none',
                  borderRadius: '14px',
                  fontFamily: 'inherit',
                  fontSize: '15px',
                  fontWeight: 700,
                  color: '#FFFFFF',
                  cursor: 'pointer',
                }}
              >
                Selesai
              </button>
            </div>
          </BottomSheet>

          {/* Quantity Picker Sheet */}
          <BottomSheet
            isOpen={conversionQuantitySheetIndex >= 0}
            onClose={() => {
              setConversionQuantitySheetIndex(-1);
              setCustomQuantityValue('');
              setTimeout(() => setConversionSheetOpen(true), 200);
            }}
            title="Pilih Jumlah"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {[6, 12, 24].map((qty) => (
                <button
                  key={qty}
                  onClick={() => {
                    if (conversionQuantitySheetIndex >= 0) {
                      updateConversion(conversionQuantitySheetIndex, { conversionFactor: qty });
                    }
                    setConversionQuantitySheetIndex(-1);
                    setCustomQuantityValue('');
                    setTimeout(() => setConversionSheetOpen(true), 200);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '16px',
                    background: formData.conversions[conversionQuantitySheetIndex]?.conversionFactor === qty
                      ? COLORS.bgCardDarker
                      : COLORS.bgCard,
                    border: 'none',
                    borderRadius: '14px',
                    fontFamily: 'inherit',
                    fontSize: '16px',
                    fontWeight: 600,
                    color: COLORS.textPrimary,
                    cursor: 'pointer',
                  }}
                >
                  {qty}
                </button>
              ))}

              {/* Custom input */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '12px 14px',
                background: COLORS.bgCard,
                borderRadius: '14px',
              }}>
                <span style={{ fontSize: '14px', fontWeight: 600, color: COLORS.textSecondary }}>Custom:</span>
                <input
                  type="number"
                  value={customQuantityValue}
                  onChange={(e) => setCustomQuantityValue(e.target.value)}
                  placeholder="Masukkan jumlah"
                  min={1}
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    fontFamily: 'inherit',
                    fontSize: '16px',
                    fontWeight: 600,
                    color: COLORS.textPrimary,
                    outline: 'none',
                  }}
                />
                {customQuantityValue && (
                  <button
                    onClick={() => {
                      const val = parseInt(customQuantityValue) || 1;
                      if (conversionQuantitySheetIndex >= 0) {
                        updateConversion(conversionQuantitySheetIndex, { conversionFactor: Math.max(1, val) });
                      }
                      setConversionQuantitySheetIndex(-1);
                      setCustomQuantityValue('');
                      setTimeout(() => setConversionSheetOpen(true), 200);
                    }}
                    style={{
                      padding: '8px 16px',
                      background: COLORS.accentOlive,
                      border: 'none',
                      borderRadius: '100px',
                      fontFamily: 'inherit',
                      fontSize: '14px',
                      fontWeight: 600,
                      color: '#FFFFFF',
                      cursor: 'pointer',
                    }}
                  >
                    OK
                  </button>
                )}
              </div>
            </div>
          </BottomSheet>

          {/* Success Toast */}
          <AnimatePresence>
            {showSuccessToast && (
              <SuccessToast message="Item berhasil disimpan" />
            )}
          </AnimatePresence>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default AddItemForm;

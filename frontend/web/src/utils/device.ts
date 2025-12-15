/**
 * Device Detection Utilities for QR Login System
 * Determines whether user is on desktop or mobile browser
 */

/**
 * Check if current browser is desktop (not mobile)
 * Desktop users will use QR-only login
 * Mobile users can login with email/password
 */
export const isDesktopBrowser = (): boolean => {
  if (typeof window === 'undefined') return true;

  const ua = navigator.userAgent.toLowerCase();
  const mobileKeywords = [
    'android',
    'webos',
    'iphone',
    'ipad',
    'ipod',
    'blackberry',
    'iemobile',
    'opera mini',
    'mobile',
    'tablet'
  ];

  // Check if any mobile keyword exists in user agent
  const isMobile = mobileKeywords.some(keyword => ua.includes(keyword));

  // Also check screen width as fallback
  const isSmallScreen = window.innerWidth < 768;

  return !isMobile && !isSmallScreen;
};

/**
 * Check if current browser is mobile
 */
export const isMobileBrowser = (): boolean => {
  return !isDesktopBrowser();
};

/**
 * Get device type string
 */
export const getDeviceType = (): 'desktop' | 'mobile' => {
  return isDesktopBrowser() ? 'desktop' : 'mobile';
};

/**
 * Generate a simple browser fingerprint for device identification
 */
export const getBrowserFingerprint = (): string => {
  if (typeof window === 'undefined') return 'unknown';

  const components = [
    navigator.userAgent,
    navigator.language,
    window.screen.colorDepth.toString(),
    `${window.screen.width}x${window.screen.height}`,
    new Date().getTimezoneOffset().toString(),
    navigator.hardwareConcurrency?.toString() || 'unknown'
  ];

  // Simple hash function
  const str = components.join('|');
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }

  return Math.abs(hash).toString(36);
};

/**
 * Get human-readable device name
 */
export const getDeviceName = (): string => {
  const ua = navigator.userAgent;

  // Detect browser
  let browser = 'Browser';
  if (ua.includes('Chrome') && !ua.includes('Edg')) browser = 'Chrome';
  else if (ua.includes('Firefox')) browser = 'Firefox';
  else if (ua.includes('Safari') && !ua.includes('Chrome')) browser = 'Safari';
  else if (ua.includes('Edg')) browser = 'Edge';

  // Detect OS
  let os = 'Desktop';
  if (ua.includes('Windows')) os = 'Windows';
  else if (ua.includes('Mac')) os = 'Mac';
  else if (ua.includes('Linux')) os = 'Linux';
  else if (ua.includes('Android')) os = 'Android';
  else if (ua.includes('iPhone') || ua.includes('iPad')) os = 'iOS';

  return `${browser} - ${os}`;
};

/**
 * Get or create a unique Browser ID (stored in localStorage)
 *
 * This identifies the browser PROFILE, not the tab.
 * - Same browser profile (normal mode) = same browser_id
 * - Incognito/Private mode = different browser_id (new localStorage)
 * - Different browser = different browser_id
 *
 * Used for single session enforcement:
 * - 1 browser profile = 1 device = 1 active session
 */
export const getBrowserId = (): string => {
  if (typeof window === 'undefined') return 'ssr-default';

  const BROWSER_ID_KEY = 'milkyhoop_browser_id';
  let browserId = localStorage.getItem(BROWSER_ID_KEY);

  if (!browserId) {
    // Generate unique browser ID using crypto API or fallback
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      browserId = crypto.randomUUID();
    } else {
      // Fallback for older browsers
      browserId = `browser_${Date.now()}_${Math.random().toString(36).substring(2, 15)}`;
    }
    localStorage.setItem(BROWSER_ID_KEY, browserId);
    console.log('[Device] Created new browser_id:', browserId);
  }

  return browserId;
};

/**
 * Get or create a unique tab ID (stored in sessionStorage)
 *
 * Unlike browser_id which is shared across tabs (localStorage),
 * tab_id is unique per browser tab. This allows multiple tabs
 * to each have their own WebSocket connection for force logout.
 *
 * Why sessionStorage:
 * - Each tab has its own sessionStorage
 * - Survives page refreshes within the same tab
 * - Auto-cleared when tab is closed
 */
export const getOrCreateTabId = (): string => {
  if (typeof window === 'undefined') return 'ssr-default';

  const TAB_ID_KEY = 'milkyhoop_tab_id';
  let tabId = sessionStorage.getItem(TAB_ID_KEY);

  if (!tabId) {
    // Generate unique tab ID: timestamp + random
    tabId = `tab_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    sessionStorage.setItem(TAB_ID_KEY, tabId);
    console.log('[Device] Created new tab_id:', tabId);
  }

  return tabId;
};

import { getToken, isTokenExpiringSoon, refreshAccessToken } from './auth';

// API base URL from environment variable
// If empty, use relative paths (nginx proxy handles it in production)
// If set, use absolute URL (for development or direct backend access)
const API_BASE_URL = process.env.REACT_APP_API_URL || '';

/**
 * Build full URL from relative path
 * - If API_BASE_URL is set: use absolute URL (development)
 * - If API_BASE_URL is empty: use relative path (production with nginx proxy)
 */
const buildUrl = (url: string): string => {
  // Already absolute URL
  if (url.startsWith('http')) return url;
  // Use base URL if configured (development)
  if (API_BASE_URL) return `${API_BASE_URL}${url}`;
  // Use relative path (production with nginx proxy)
  return url;
};

/**
 * Fetch wrapper with automatic token refresh and retry logic
 * Industry standard: seamless authentication like Claude/ChatGPT
 */
const fetchWithAuth = async (url: string, options: RequestInit = {}, retryCount = 0): Promise<Response> => {
  const MAX_RETRIES = 2;
  
  // Check if token needs refresh before request
  if (isTokenExpiringSoon()) {
    console.log('[fetchWithAuth] Token expiring soon, refreshing...');
    const refreshed = await refreshAccessToken();
    if (!refreshed) {
      throw new Error('Token refresh failed - user logged out');
    }
  }
  
  // Get fresh token
  const token = getToken();
  
  // Make request with token
  const fullUrl = buildUrl(url);
  const response = await fetch(fullUrl, {
    ...options,
    headers: {
      ...options.headers,
      ...(token && { 'Authorization': `Bearer ${token}` })
    }
  });
  
  // If 401 Unauthorized - try refresh and retry
  if (response.status === 401) {
    if (retryCount < MAX_RETRIES) {
      console.log(`[fetchWithAuth] Got 401, attempting token refresh (retry ${retryCount + 1}/${MAX_RETRIES})...`);
      const refreshed = await refreshAccessToken();
      
      if (refreshed) {
        // Retry request with new token
        const newToken = getToken();
        const fullUrl = buildUrl(url);
        return fetch(fullUrl, {
          ...options,
          headers: {
            ...options.headers,
            ...(newToken && { 'Authorization': `Bearer ${newToken}` })
          }
        });
      } else {
        // Refresh failed - user already logged out by refreshAccessToken()
        throw new Error('Authentication failed - please login again');
      }
    } else {
      console.error('[fetchWithAuth] Max retries reached for 401');
      throw new Error('Authentication failed after retries');
    }
  }
  
  return response;
};

/**
 * Check if user has tenant access
 * Since backend doesn't have /access endpoint, we validate by:
 * 1. Check if token exists
 * 2. Make a test call to tenant chat endpoint
 * Backend will return 403 if user not authorized
 */
export const checkTenantAccess = async (tenantId: string): Promise<boolean> => {
  const token = getToken();
  if (!token) return false;
  
  try {
    const response = await fetchWithAuth(`/api/tenant/${tenantId}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ message: '__check_access__' })
    });
    
    return response.ok;
  } catch (error) {
    console.error('[checkTenantAccess] Failed:', error);
    return false;
  }
};

/**
 * Send message to tenant chat endpoint
 * Automatically includes auth token and handles refresh
 */
export const sendTenantMessage = async (tenantId: string, message: string) => {
  const response = await fetchWithAuth(`/api/tenant/${tenantId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ message })
  });
  
  if (!response.ok) {
    throw new Error(`API call failed: ${response.status}`);
  }
  
  return response.json();
};

/**
 * Send message to customer (public) chat endpoint
 * No authentication required
 */
export const sendCustomerMessage = async (tenantId: string, message: string) => {
  // Customer endpoint is public
  const endpoint = `/${tenantId}/chat`;
  const fullUrl = buildUrl(endpoint);
  const response = await fetch(fullUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ message })
  });
  
  if (!response.ok) {
    throw new Error(`API call failed: ${response.status}`);
  }
  
  return response.json();
};
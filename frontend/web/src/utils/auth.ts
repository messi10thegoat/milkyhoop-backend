export const getToken = (): string | null => {
  return localStorage.getItem('access_token');
};

export const getRefreshToken = (): string | null => {
  return localStorage.getItem('refresh_token');
};

export const setTokens = (accessToken: string, refreshToken: string): void => {
  localStorage.setItem('access_token', accessToken);
  localStorage.setItem('refresh_token', refreshToken);
  localStorage.setItem('token_timestamp', Date.now().toString());
};

export const isTokenExpiringSoon = (): boolean => {
  const token = getToken();
  if (!token) return true;

  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const expiryTime = payload.exp * 1000;
    const now = Date.now();
    const fiveMinutes = 5 * 60 * 1000;

    return (expiryTime - now) < fiveMinutes;
  } catch {
    return true;
  }
};

/**
 * Refresh access token with retry mechanism (industry standard like Claude/ChatGPT)
 * Retries network errors, only logout on explicit token rejection (401)
 */
export const refreshAccessToken = async (retryCount = 0): Promise<boolean> => {
  const MAX_RETRIES = 3;
  const RETRY_DELAYS = [1000, 2000, 4000]; // Exponential backoff: 1s, 2s, 4s

  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    logout('no_refresh_token');
    return false;
  }

  try {
    const response = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ refresh_token: refreshToken })
    });

    // Explicit token rejection (invalid, expired, revoked)
    if (response.status === 401 || response.status === 403) {
      console.error('[refreshAccessToken] Token invalid/expired, logging out');
      logout('token_expired');
      return false;
    }

    // Server error or network issue - retry
    if (!response.ok) {
      if (retryCount < MAX_RETRIES) {
        const delay = RETRY_DELAYS[retryCount];
        console.warn(`[refreshAccessToken] Server error (${response.status}), retrying in ${delay}ms... (${retryCount + 1}/${MAX_RETRIES})`);
        await new Promise(resolve => setTimeout(resolve, delay));
        return refreshAccessToken(retryCount + 1);
      } else {
        console.error('[refreshAccessToken] Max retries reached, logging out');
        logout('refresh_failed');
        return false;
      }
    }

    const data = await response.json();
    if (data.success && data.data.access_token) {
      setTokens(data.data.access_token, data.data.refresh_token);
      console.log('[refreshAccessToken] Token refreshed successfully');
      return true;
    }

    // Unexpected response format
    console.error('[refreshAccessToken] Unexpected response format');
    logout('refresh_failed');
    return false;

  } catch (error) {
    // Network error (offline, timeout, etc) - retry
    if (retryCount < MAX_RETRIES) {
      const delay = RETRY_DELAYS[retryCount];
      console.warn(`[refreshAccessToken] Network error, retrying in ${delay}ms... (${retryCount + 1}/${MAX_RETRIES})`, error);
      await new Promise(resolve => setTimeout(resolve, delay));
      return refreshAccessToken(retryCount + 1);
    } else {
      console.error('[refreshAccessToken] Max retries reached after network errors, logging out');
      logout('network_error');
      return false;
    }
  }
};

export const getUserEmail = (): string | null => {
  const token = getToken();
  if (!token) return null;

  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.email || null;
  } catch {
    return null;
  }
};

export const isAuthenticated = (): boolean => {
  return !!getToken();
};

/**
 * Logout with reason tracking
 * @param reason - Why logout occurred: 'manual', 'token_expired', 'refresh_failed', 'network_error', 'no_refresh_token'
 */
export const logout = async (reason: string = 'manual') => {
  const refreshToken = getRefreshToken();
  const token = getToken();

  // Call backend logout endpoint (only for manual logout with valid tokens)
  if (refreshToken && token && reason === 'manual') {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      const userId = payload.user_id;

      await fetch(`/api/auth/logout?user_id=${userId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          refresh_token: refreshToken,
          logout_all_devices: false
        })
      });
    } catch (error) {
      console.error('[logout] Backend logout API call failed:', error);
    }
  }

  // Clear local storage
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('token_timestamp');

  // Dispatch logout event with reason for UI components to react
  window.dispatchEvent(new CustomEvent('logout', { detail: { reason } }));

  console.log(`[logout] User logged out, reason: ${reason}`);
};

/**
 * Verify session is still valid on server (not replaced by another device)
 * @param source - Who triggered the verification: 'visibility', 'poll', 'manual'
 * @returns true if session valid, false if invalid/replaced
 */
export const verifySession = async (source: string = 'manual'): Promise<boolean> => {
  const token = getToken();
  if (!token) return false;

  try {
    console.log(`[verifySession] Checking session (${source})...`);

    const response = await fetch('/api/auth/verify', {
      headers: { 'Authorization': `Bearer ${token}` },
      cache: 'no-store' // Prevent browser caching - always fresh request
    });

    // 204 No Content = session valid
    if (response.status === 204) {
      console.log(`[verifySession] Session valid`);
      return true;
    }

    // 401 = session invalid/replaced
    if (response.status === 401) {
      const data = await response.json().catch(() => ({}));
      const reason = data.code || 'session_invalid';
      console.warn(`[verifySession] Session invalid: ${reason}`);

      // Dispatch session_replaced event for UI to show modal
      window.dispatchEvent(new CustomEvent('session_replaced', {
        detail: { reason, source }
      }));

      // Clear tokens
      await logout(reason);
      return false;
    }

    // Other errors - don't logout, might be network issue
    console.warn(`[verifySession] Unexpected status: ${response.status}`);
    return true; // Assume valid on network errors

  } catch (error) {
    console.warn(`[verifySession] Network error:`, error);
    return true; // Assume valid on network errors
  }
};

// Session enforcement cleanup function
let sessionEnforcementCleanup: (() => void) | null = null;

/**
 * Setup session enforcement with visibilitychange + 1-hour polling
 * Call this once on app init
 */
export const setupSessionEnforcement = (): (() => void) => {
  // Clean up existing if called multiple times
  if (sessionEnforcementCleanup) {
    sessionEnforcementCleanup();
  }

  // Primary: visibilitychange handler
  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible' && isAuthenticated()) {
      verifySession('visibility');
    }
  };

  document.addEventListener('visibilitychange', handleVisibilityChange);

  // Safety net: 1-hour polling (only when tab is visible)
  const POLL_INTERVAL = 60 * 60 * 1000; // 1 hour
  const pollInterval = setInterval(() => {
    if (document.visibilityState === 'visible' && isAuthenticated()) {
      verifySession('poll');
    }
  }, POLL_INTERVAL);

  console.log('[setupSessionEnforcement] Session enforcement active (visibilitychange + 1hr poll)');

  // Return cleanup function
  sessionEnforcementCleanup = () => {
    document.removeEventListener('visibilitychange', handleVisibilityChange);
    clearInterval(pollInterval);
    console.log('[setupSessionEnforcement] Session enforcement cleaned up');
  };

  return sessionEnforcementCleanup;
};

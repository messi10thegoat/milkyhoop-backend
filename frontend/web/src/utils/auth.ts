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
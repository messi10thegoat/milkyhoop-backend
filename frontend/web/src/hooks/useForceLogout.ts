/**
 * Hook to handle force logout via WebSocket, BroadcastChannel, AND Storage Events
 *
 * Three mechanisms for WhatsApp-style single session:
 * 1. WebSocket: For cross-browser force logout (Chrome -> Firefox)
 * 2. BroadcastChannel: For same-browser force logout (Tab 1 -> Tab 2) - PRIMARY
 * 3. Storage Event: Fallback for browsers without BroadcastChannel
 *
 * Multi-tab support:
 * - device_id: Shared across tabs (localStorage) - identifies the login session
 * - tab_id: Unique per tab (sessionStorage) - identifies the browser tab
 *
 * When a new login happens, old sessions are kicked out and page re-renders
 * as QR login (like WhatsApp Web).
 */
import { useEffect, useRef, useCallback } from 'react';
import { DeviceWebSocketClient, DeviceEvent } from '../utils/websocket';
import { getOrCreateTabId } from '../utils/device';

// BroadcastChannel name for force logout
const FORCE_LOGOUT_CHANNEL = 'milkyhoop_force_logout';

interface ForceLogoutMessage {
  type: 'force_logout' | 'logout';
  reason: string;
  from_tab_id?: string;
}

export function useForceLogout() {
  const wsClient = useRef<DeviceWebSocketClient | null>(null);
  const broadcastChannel = useRef<BroadcastChannel | null>(null);
  // Track our identifiers
  const myDeviceId = useRef<string | null>(null);
  const myTabId = useRef<string | null>(null);

  // Handle logout - clear tokens and reload
  const performLogout = useCallback((reason: string) => {
    console.log('[ForceLogout] Logging out:', reason);

    // Clear auth state
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('token_timestamp');
    localStorage.removeItem('user_info');
    localStorage.removeItem('device_id');

    // Re-render page in place (WhatsApp style)
    window.location.reload();
  }, []);

  // Broadcast force logout to other tabs in same browser
  const broadcastForceLogout = useCallback((reason: string) => {
    if (broadcastChannel.current) {
      const message: ForceLogoutMessage = {
        type: 'force_logout',
        reason,
        from_tab_id: myTabId.current || undefined
      };
      broadcastChannel.current.postMessage(message);
      console.log('[ForceLogout] Broadcast sent to other tabs:', reason);
    }
  }, []);

  // Handle WebSocket force_logout event (cross-browser)
  const handleDeviceEvent = useCallback((event: DeviceEvent) => {
    if (event.event === 'force_logout') {
      console.log('[ForceLogout] WebSocket force_logout received:', event.reason);
      // Also broadcast to other tabs in same browser
      broadcastForceLogout(event.reason || 'Session digantikan');
      performLogout(event.reason || 'Session digantikan');
    }
  }, [performLogout, broadcastForceLogout]);

  // Handle BroadcastChannel message (same-browser, PRIMARY method)
  const handleBroadcastMessage = useCallback((event: MessageEvent<ForceLogoutMessage>) => {
    const message = event.data;
    console.log('[ForceLogout] BroadcastChannel message:', message);

    // Ignore messages from self
    if (message.from_tab_id === myTabId.current) {
      console.log('[ForceLogout] Ignoring message from self');
      return;
    }

    if (message.type === 'force_logout' || message.type === 'logout') {
      performLogout(message.reason);
    }
  }, [performLogout]);

  // Handle storage event (same-browser, FALLBACK for no BroadcastChannel)
  const handleStorageEvent = useCallback((event: StorageEvent) => {
    // Only care about device_id changes
    if (event.key !== 'device_id') return;

    const newDeviceId = event.newValue;
    const oldDeviceId = myDeviceId.current;

    console.log('[ForceLogout] Storage event - device_id changed:', {
      old: oldDeviceId,
      new: newDeviceId
    });

    // If device_id changed and we had one, another tab logged in
    if (oldDeviceId && newDeviceId && newDeviceId !== oldDeviceId) {
      console.log('[ForceLogout] Another tab logged in (storage event), forcing logout');
      performLogout('Login dari tab lain');
    }

    // If device_id was removed (logout from another tab)
    if (oldDeviceId && !newDeviceId) {
      console.log('[ForceLogout] Logged out from another tab (storage event)');
      performLogout('Logout dari tab lain');
    }
  }, [performLogout]);

  useEffect(() => {
    const deviceId = localStorage.getItem('device_id');
    const tabId = getOrCreateTabId();
    myDeviceId.current = deviceId;
    myTabId.current = tabId;

    console.log('[ForceLogout] useEffect INIT - deviceId:', deviceId, 'tabId:', tabId);

    // === 1. BroadcastChannel (same-browser, PRIMARY) ===
    // Most reliable for same-browser multi-tab communication
    if (typeof BroadcastChannel !== 'undefined') {
      broadcastChannel.current = new BroadcastChannel(FORCE_LOGOUT_CHANNEL);
      broadcastChannel.current.onmessage = handleBroadcastMessage;
      console.log('[ForceLogout] BroadcastChannel connected');
    } else {
      console.log('[ForceLogout] BroadcastChannel not supported, using storage only');
    }

    // === 2. Storage Event (same-browser, FALLBACK) ===
    // Fires when localStorage changes in another tab
    const storageHandler = (event: StorageEvent) => {
      console.log('[ForceLogout] RAW Storage Event:', {
        key: event.key,
        oldValue: event.oldValue,
        newValue: event.newValue
      });
      handleStorageEvent(event);
    };
    window.addEventListener('storage', storageHandler);

    // === 3. WebSocket (cross-browser) ===
    if (deviceId) {
      console.log('[ForceLogout] Connecting to device WebSocket:', deviceId, 'tabId:', tabId);
      wsClient.current = new DeviceWebSocketClient(deviceId, handleDeviceEvent, tabId);
      wsClient.current.connect();
    } else {
      console.log('[ForceLogout] No device_id, WebSocket skipped');
    }

    return () => {
      console.log('[ForceLogout] useEffect CLEANUP - disconnecting');
      window.removeEventListener('storage', storageHandler);
      broadcastChannel.current?.close();
      wsClient.current?.disconnect();
    };
  }, [handleDeviceEvent, handleBroadcastMessage, handleStorageEvent]);

  return null;
}

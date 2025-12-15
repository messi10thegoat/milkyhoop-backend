/**
 * WebSocket Client for QR Login System
 * Handles real-time QR status updates and device force logout
 */

export type QREvent =
  | { event: 'connected'; message: string }
  | { event: 'scanned'; message: string }
  | { event: 'approved'; message: string; access_token: string; refresh_token: string; user: any }
  | { event: 'rejected'; message: string }
  | { event: 'expired'; message: string }
  | { event: 'ping' }
  | { event: 'pong' }
  | { event: 'error'; message: string };

export type DeviceEvent =
  | { event: 'force_logout'; reason: string }
  | { event: 'ping' }
  | { event: 'pong' };

type QREventHandler = (event: QREvent) => void;
type DeviceEventHandler = (event: DeviceEvent) => void;

/**
 * QR Login WebSocket Client
 * Used by desktop browser to receive real-time status updates
 */
export class QRWebSocketClient {
  private ws: WebSocket | null = null;
  private token: string;
  private onEvent: QREventHandler;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 3;
  private pingInterval: NodeJS.Timeout | null = null;

  constructor(token: string, onEvent: QREventHandler) {
    this.token = token;
    this.onEvent = onEvent;
  }

  connect(): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/auth/qr/ws/${this.token}`;

    console.log('[QRWebSocket] Connecting to:', wsUrl);

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('[QRWebSocket] Connected');
      this.reconnectAttempts = 0;
      this.startPing();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as QREvent;
        console.log('[QRWebSocket] Event received:', data.event);
        this.onEvent(data);

        // Handle terminal events
        if (data.event === 'approved' || data.event === 'rejected' || data.event === 'expired') {
          this.disconnect();
        }
      } catch (e) {
        console.error('[QRWebSocket] Failed to parse message:', e);
      }
    };

    this.ws.onerror = (error) => {
      console.error('[QRWebSocket] Error:', error);
      this.onEvent({ event: 'error', message: 'WebSocket connection error' });
    };

    this.ws.onclose = (event) => {
      console.log('[QRWebSocket] Closed:', event.code, event.reason);
      this.stopPing();

      // Attempt reconnect if not intentional disconnect
      if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        console.log(`[QRWebSocket] Reconnecting... attempt ${this.reconnectAttempts}`);
        setTimeout(() => this.connect(), 2000);
      }
    };
  }

  private startPing(): void {
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ event: 'ping' }));
      }
    }, 30000); // Ping every 30 seconds
  }

  private stopPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  disconnect(): void {
    this.stopPing();
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

/**
 * Device WebSocket Client
 * Used by web sessions to receive force logout commands
 *
 * Supports multi-tab: each tab has its own WebSocket connection
 * - deviceId: shared across tabs (localStorage)
 * - tabId: unique per tab (sessionStorage)
 */
export class DeviceWebSocketClient {
  private ws: WebSocket | null = null;
  private deviceId: string;
  private tabId: string;
  private onEvent: DeviceEventHandler;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private pingInterval: NodeJS.Timeout | null = null;

  constructor(deviceId: string, onEvent: DeviceEventHandler, tabId: string = 'default') {
    this.deviceId = deviceId;
    this.tabId = tabId;
    this.onEvent = onEvent;
  }

  connect(): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/devices/ws/${this.deviceId}?tab_id=${this.tabId}`;

    console.log('[DeviceWebSocket] Connecting to:', wsUrl);

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('[DeviceWebSocket] Connected');
      this.reconnectAttempts = 0;
      this.startPing();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as DeviceEvent;
        console.log('[DeviceWebSocket] Event received:', data.event);
        this.onEvent(data);

        // Handle force logout
        if (data.event === 'force_logout') {
          this.disconnect();
        }
      } catch (e) {
        console.error('[DeviceWebSocket] Failed to parse message:', e);
      }
    };

    this.ws.onerror = (error) => {
      console.error('[DeviceWebSocket] Error:', error);
    };

    this.ws.onclose = (event) => {
      console.log('[DeviceWebSocket] Closed:', event.code, event.reason);
      this.stopPing();

      // Attempt reconnect
      if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        setTimeout(() => this.connect(), 3000 * this.reconnectAttempts);
      }
    };
  }

  private startPing(): void {
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ event: 'ping' }));
      }
    }, 30000);
  }

  private stopPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  disconnect(): void {
    this.stopPing();
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
  }
}

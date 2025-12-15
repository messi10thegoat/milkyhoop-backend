import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { QRWebSocketClient, QREvent } from '../utils/websocket';
import { setTokens } from '../utils/auth';
import { getBrowserFingerprint, getBrowserId } from '../utils/device';

// QR Code SVG Generator (simple implementation)
const generateQRCodeSVG = (data: string, size: number = 200): string => {
  // Using external QR library would be better, but for demo we'll use a placeholder
  // In production, use a library like 'qrcode' or render via canvas
  return `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${encodeURIComponent(data)}`;
};

type Status = 'loading' | 'ready' | 'scanned' | 'approved' | 'rejected' | 'expired' | 'error';

interface QRData {
  token: string;
  qr_url: string;
  expires_at: string;
  ttl_seconds: number;
}

const QRLoginPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState<Status>('loading');
  const [qrData, setQrData] = useState<QRData | null>(null);
  const [message, setMessage] = useState('Memuat QR code...');
  const [countdown, setCountdown] = useState(120);
  const wsClient = useRef<QRWebSocketClient | null>(null);
  const countdownTimer = useRef<NodeJS.Timeout | null>(null);

  // Check for logout reason
  const logoutReason = searchParams.get('reason');

  // Generate new QR token
  const generateQR = useCallback(async () => {
    setStatus('loading');
    setMessage('Memuat QR code...');

    try {
      const fingerprint = getBrowserFingerprint();
      const browser_id = getBrowserId();  // For single session enforcement
      const response = await fetch('/api/auth/qr/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fingerprint, browser_id })
      });

      if (!response.ok) {
        throw new Error('Failed to generate QR code');
      }

      const data = await response.json();
      if (data.success) {
        setQrData({
          token: data.token,
          qr_url: data.qr_url,
          expires_at: data.expires_at,
          ttl_seconds: data.ttl_seconds
        });
        setCountdown(data.ttl_seconds);
        setStatus('ready');
        setMessage('Scan QR code dengan perangkat mobile Anda');
        return data.token;
      } else {
        throw new Error(data.detail || 'Failed to generate QR code');
      }
    } catch (error) {
      console.error('QR generation error:', error);
      setStatus('error');
      setMessage('Gagal memuat QR code. Klik untuk coba lagi.');
      return null;
    }
  }, []);

  // Handle WebSocket events
  const handleQREvent = useCallback((event: QREvent) => {
    switch (event.event) {
      case 'connected':
        console.log('WebSocket connected');
        break;
      case 'scanned':
        setStatus('scanned');
        setMessage('QR code di-scan! Menunggu konfirmasi dari mobile...');
        break;
      case 'approved':
        setStatus('approved');
        setMessage('Login berhasil! Mengalihkan...');
        // Store tokens
        setTokens(event.access_token, event.refresh_token);
        // Store user info
        if (event.user) {
          localStorage.setItem('user_info', JSON.stringify(event.user));
        }
        // Redirect to root - App will show dashboard for authenticated users
        setTimeout(() => {
          navigate('/');
        }, 500);
        break;
      case 'rejected':
        setStatus('rejected');
        setMessage('Login ditolak oleh pengguna mobile');
        // Auto-regenerate QR after 3 seconds
        setTimeout(() => generateQR(), 3000);
        break;
      case 'expired':
        setStatus('expired');
        setMessage('QR code kadaluarsa. Klik untuk generate baru.');
        break;
      case 'error':
        setStatus('error');
        setMessage('Terjadi kesalahan koneksi');
        break;
    }
  }, [navigate, generateQR]);

  // Initialize QR and WebSocket
  useEffect(() => {
    let mounted = true;

    const init = async () => {
      const token = await generateQR();
      if (token && mounted) {
        // Connect WebSocket
        wsClient.current = new QRWebSocketClient(token, handleQREvent);
        wsClient.current.connect();
      }
    };

    init();

    return () => {
      mounted = false;
      wsClient.current?.disconnect();
      if (countdownTimer.current) {
        clearInterval(countdownTimer.current);
      }
    };
  }, [generateQR, handleQREvent]);

  // Countdown timer
  useEffect(() => {
    if (status === 'ready' && countdown > 0) {
      countdownTimer.current = setInterval(() => {
        setCountdown(prev => {
          if (prev <= 1) {
            setStatus('expired');
            setMessage('QR code kadaluarsa. Klik untuk generate baru.');
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }

    return () => {
      if (countdownTimer.current) {
        clearInterval(countdownTimer.current);
      }
    };
  }, [status]);

  // Handle click to regenerate
  const handleRegenerateClick = async () => {
    if (status === 'expired' || status === 'error' || status === 'rejected') {
      wsClient.current?.disconnect();
      const token = await generateQR();
      if (token) {
        wsClient.current = new QRWebSocketClient(token, handleQREvent);
        wsClient.current.connect();
      }
    }
  };

  // Format countdown
  const formatCountdown = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  // Status icon
  const getStatusIcon = () => {
    switch (status) {
      case 'loading':
        return (
          <div className="animate-spin w-12 h-12 border-4 border-purple-200 border-t-purple-600 rounded-full" />
        );
      case 'scanned':
        return (
          <div className="w-12 h-12 bg-yellow-100 rounded-full flex items-center justify-center">
            <svg className="w-6 h-6 text-yellow-600 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
        );
      case 'approved':
        return (
          <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center">
            <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
        );
      case 'rejected':
        return (
          <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center">
            <svg className="w-6 h-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
        );
      case 'expired':
      case 'error':
        return (
          <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center cursor-pointer hover:bg-gray-200" onClick={handleRegenerateClick}>
            <svg className="w-6 h-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-50 to-white flex items-center justify-center p-4">
      <div className="max-w-md w-full">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 bg-gradient-to-br from-purple-500 to-purple-700 rounded-2xl mx-auto flex items-center justify-center shadow-lg mb-4">
            <span className="text-white text-3xl font-bold">m</span>
          </div>
          <h1 className="text-2xl font-bold text-gray-900">MilkyHoop</h1>
          <p className="text-gray-500 mt-1">Login dengan QR Code</p>
        </div>

        {/* Logout reason message */}
        {logoutReason && (
          <div className="mb-6 p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 text-sm text-center">
            {logoutReason === 'logged_out_remotely' && 'Sesi Anda telah diakhiri dari perangkat lain.'}
            {logoutReason === 'session_expired' && 'Sesi Anda telah berakhir. Silakan login kembali.'}
          </div>
        )}

        {/* QR Card */}
        <div className="bg-white rounded-2xl shadow-xl p-6">
          {/* QR Code Display */}
          <div className="relative">
            {status === 'ready' && qrData ? (
              <div className="relative">
                <img
                  src={generateQRCodeSVG(qrData.qr_url, 250)}
                  alt="QR Code"
                  className="w-full max-w-[250px] mx-auto rounded-lg"
                />
                {/* Countdown overlay */}
                <div className="absolute top-2 right-2 bg-white/90 px-2 py-1 rounded-full text-sm font-medium text-gray-600">
                  {formatCountdown(countdown)}
                </div>
              </div>
            ) : (
              <div
                className={`w-full aspect-square max-w-[250px] mx-auto rounded-lg bg-gray-100 flex items-center justify-center ${(status === 'expired' || status === 'error') ? 'cursor-pointer hover:bg-gray-200' : ''}`}
                onClick={handleRegenerateClick}
              >
                {getStatusIcon()}
              </div>
            )}
          </div>

          {/* Status message */}
          <div className="mt-6 text-center">
            <p className={`font-medium ${status === 'approved' ? 'text-green-600' : status === 'rejected' || status === 'error' ? 'text-red-600' : 'text-gray-700'}`}>
              {message}
            </p>
          </div>

          {/* Instructions */}
          <div className="mt-6 pt-6 border-t border-gray-100">
            <h3 className="font-medium text-gray-900 mb-3">Cara login:</h3>
            <ol className="text-sm text-gray-600 space-y-2">
              <li className="flex items-start">
                <span className="w-5 h-5 bg-purple-100 rounded-full flex items-center justify-center text-purple-600 text-xs font-medium mr-2 mt-0.5">1</span>
                <span>Buka MilkyHoop di HP Anda</span>
              </li>
              <li className="flex items-start">
                <span className="w-5 h-5 bg-purple-100 rounded-full flex items-center justify-center text-purple-600 text-xs font-medium mr-2 mt-0.5">2</span>
                <span>Tap menu "Hubungkan Perangkat"</span>
              </li>
              <li className="flex items-start">
                <span className="w-5 h-5 bg-purple-100 rounded-full flex items-center justify-center text-purple-600 text-xs font-medium mr-2 mt-0.5">3</span>
                <span>Scan QR code ini dengan kamera HP</span>
              </li>
              <li className="flex items-start">
                <span className="w-5 h-5 bg-purple-100 rounded-full flex items-center justify-center text-purple-600 text-xs font-medium mr-2 mt-0.5">4</span>
                <span>Konfirmasi login di HP Anda</span>
              </li>
            </ol>
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-sm text-gray-500 mt-6">
          Belum punya akun?{' '}
          <a href="https://milkyhoop.com" className="text-purple-600 hover:underline">
            Daftar di HP
          </a>
        </p>
      </div>
    </div>
  );
};

export default QRLoginPage;

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { getToken } from '../utils/auth';

interface QRScannerProps {
  isOpen: boolean;
  onClose: () => void;
  onScanSuccess?: () => void;
}

type ScanStatus = 'idle' | 'scanning' | 'scanned' | 'approving' | 'approved' | 'rejected' | 'error';

interface ApprovalData {
  token: string;
  webInfo?: {
    userAgent?: string;
    ip?: string;
  };
}

const QRScanner: React.FC<QRScannerProps> = ({ isOpen, onClose, onScanSuccess }) => {
  const [status, setStatus] = useState<ScanStatus>('idle');
  const [message, setMessage] = useState('');
  const [approvalData, setApprovalData] = useState<ApprovalData | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const scanIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Start camera
  const startCamera = useCallback(async () => {
    try {
      setCameraError(null);
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: 'environment', // Use back camera on mobile
          width: { ideal: 1280 },
          height: { ideal: 720 }
        }
      });

      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      setStatus('scanning');
      setMessage('Arahkan kamera ke QR code');

      // Start scanning for QR codes
      startQRScanning();
    } catch (error: any) {
      console.error('Camera error:', error);
      setCameraError(
        error.name === 'NotAllowedError'
          ? 'Izinkan akses kamera untuk scan QR code'
          : 'Gagal mengakses kamera'
      );
      setStatus('error');
    }
  }, []);

  // Stop camera
  const stopCamera = useCallback(() => {
    if (scanIntervalRef.current) {
      clearInterval(scanIntervalRef.current);
      scanIntervalRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  // Start QR scanning using canvas
  const startQRScanning = useCallback(() => {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');

    scanIntervalRef.current = setInterval(async () => {
      if (!videoRef.current || videoRef.current.readyState !== 4 || !ctx) return;

      canvas.width = videoRef.current.videoWidth;
      canvas.height = videoRef.current.videoHeight;
      ctx.drawImage(videoRef.current, 0, 0);

      // Get image data for QR detection
      // In production, use a library like jsQR or ZXing
      // For now, we'll use manual input as fallback
      // This is a simplified implementation

      const imageData = canvas.toDataURL('image/jpeg', 0.8);

      // Try to decode QR via API (if you have a QR decode endpoint)
      // Or use a client-side library like jsQR
      try {
        // Check if we have the jsQR library
        if (typeof (window as any).jsQR !== 'undefined') {
          const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const code = (window as any).jsQR(imgData.data, canvas.width, canvas.height);

          if (code && code.data) {
            handleQRCodeFound(code.data);
          }
        }
      } catch (e) {
        // Silent fail - continue scanning
      }
    }, 500); // Scan every 500ms
  }, []);

  // Handle QR code found
  const handleQRCodeFound = async (qrData: string) => {
    // Stop scanning
    if (scanIntervalRef.current) {
      clearInterval(scanIntervalRef.current);
      scanIntervalRef.current = null;
    }

    setStatus('scanned');
    setMessage('QR code terdeteksi! Memproses...');

    // Extract token from QR URL
    // Format: milkyhoop://login?token=xxx
    const match = qrData.match(/milkyhoop:\/\/login\?token=([^&]+)/);
    if (!match) {
      setStatus('error');
      setMessage('QR code tidak valid. Silakan scan QR dari halaman login desktop.');
      return;
    }

    const token = match[1];

    // Call scan API
    try {
      const accessToken = getToken();
      const response = await fetch('/api/auth/qr/scan', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({ token })
      });

      const data = await response.json();

      if (response.ok && data.success) {
        setApprovalData({ token });
        setStatus('scanned');
        setMessage('QR berhasil di-scan. Konfirmasi login?');
      } else {
        setStatus('error');
        setMessage(data.detail || 'Gagal memproses QR code');
      }
    } catch (error) {
      console.error('Scan error:', error);
      setStatus('error');
      setMessage('Terjadi kesalahan. Silakan coba lagi.');
    }
  };

  // Handle manual token input (fallback)
  const handleManualInput = async (inputToken: string) => {
    if (!inputToken.trim()) return;

    // If full URL, extract token
    let token = inputToken.trim();
    const match = token.match(/token=([^&]+)/);
    if (match) {
      token = match[1];
    }

    handleQRCodeFound(`milkyhoop://login?token=${token}`);
  };

  // Approve login
  const handleApprove = async () => {
    if (!approvalData) return;

    setStatus('approving');
    setMessage('Memproses persetujuan...');

    try {
      const accessToken = getToken();
      const response = await fetch('/api/auth/qr/approve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          token: approvalData.token,
          approved: true
        })
      });

      const data = await response.json();

      if (response.ok && data.success) {
        setStatus('approved');
        setMessage('Login berhasil disetujui!');
        onScanSuccess?.();
        setTimeout(() => onClose(), 2000);
      } else {
        setStatus('error');
        setMessage(data.detail || 'Gagal menyetujui login');
      }
    } catch (error) {
      console.error('Approve error:', error);
      setStatus('error');
      setMessage('Terjadi kesalahan. Silakan coba lagi.');
    }
  };

  // Reject login
  const handleReject = async () => {
    if (!approvalData) return;

    setStatus('approving');
    setMessage('Memproses penolakan...');

    try {
      const accessToken = getToken();
      const response = await fetch('/api/auth/qr/approve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          token: approvalData.token,
          approved: false
        })
      });

      const data = await response.json();

      if (response.ok && data.success) {
        setStatus('rejected');
        setMessage('Login ditolak');
        setTimeout(() => onClose(), 1500);
      } else {
        setStatus('error');
        setMessage(data.detail || 'Gagal menolak login');
      }
    } catch (error) {
      console.error('Reject error:', error);
      setStatus('error');
      setMessage('Terjadi kesalahan. Silakan coba lagi.');
    }
  };

  // Start camera when opened
  useEffect(() => {
    if (isOpen) {
      startCamera();
    } else {
      stopCamera();
      setStatus('idle');
      setMessage('');
      setApprovalData(null);
      setCameraError(null);
    }

    return () => {
      stopCamera();
    };
  }, [isOpen, startCamera, stopCamera]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl max-w-md w-full overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-bold text-gray-900">
            {status === 'scanned' && approvalData ? 'Konfirmasi Login' : 'Scan QR Code'}
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-4">
          {/* Camera view */}
          {status === 'scanning' && !approvalData && (
            <div className="relative aspect-square bg-black rounded-lg overflow-hidden mb-4">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="w-full h-full object-cover"
              />
              {/* Scanning overlay */}
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-48 h-48 border-2 border-white rounded-lg relative">
                  <div className="absolute top-0 left-0 w-6 h-6 border-t-4 border-l-4 border-purple-500 rounded-tl-lg" />
                  <div className="absolute top-0 right-0 w-6 h-6 border-t-4 border-r-4 border-purple-500 rounded-tr-lg" />
                  <div className="absolute bottom-0 left-0 w-6 h-6 border-b-4 border-l-4 border-purple-500 rounded-bl-lg" />
                  <div className="absolute bottom-0 right-0 w-6 h-6 border-b-4 border-r-4 border-purple-500 rounded-br-lg" />
                  {/* Scan line animation */}
                  <div className="absolute left-0 right-0 h-0.5 bg-purple-500 animate-pulse" style={{ top: '50%' }} />
                </div>
              </div>
            </div>
          )}

          {/* Camera error */}
          {cameraError && (
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              </div>
              <p className="text-red-600 font-medium">{cameraError}</p>
              <button
                onClick={startCamera}
                className="mt-4 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700"
              >
                Coba Lagi
              </button>
            </div>
          )}

          {/* Approval confirmation */}
          {status === 'scanned' && approvalData && (
            <div className="text-center py-4">
              <div className="w-16 h-16 bg-purple-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              </div>
              <h3 className="text-lg font-bold text-gray-900 mb-2">Login ke Desktop?</h3>
              <p className="text-gray-600 text-sm mb-6">
                Anda akan login ke MilkyHoop di browser desktop.
                Pastikan ini adalah perangkat Anda.
              </p>

              <div className="flex gap-3">
                <button
                  onClick={handleReject}
                  className="flex-1 px-4 py-3 bg-gray-100 text-gray-700 rounded-lg font-medium hover:bg-gray-200"
                >
                  Batal
                </button>
                <button
                  onClick={handleApprove}
                  className="flex-1 px-4 py-3 bg-purple-600 text-white rounded-lg font-medium hover:bg-purple-700"
                >
                  Setuju
                </button>
              </div>
            </div>
          )}

          {/* Approving status */}
          {status === 'approving' && (
            <div className="text-center py-8">
              <div className="animate-spin w-12 h-12 border-4 border-purple-200 border-t-purple-600 rounded-full mx-auto mb-4" />
              <p className="text-gray-600">{message}</p>
            </div>
          )}

          {/* Success status */}
          {status === 'approved' && (
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="text-green-600 font-medium">{message}</p>
            </div>
          )}

          {/* Rejected status */}
          {status === 'rejected' && (
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </div>
              <p className="text-gray-600 font-medium">{message}</p>
            </div>
          )}

          {/* Error status */}
          {status === 'error' && !cameraError && (
            <div className="text-center py-8">
              <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <p className="text-red-600 font-medium">{message}</p>
              <button
                onClick={() => {
                  setStatus('scanning');
                  setMessage('Arahkan kamera ke QR code');
                  setApprovalData(null);
                  startQRScanning();
                }}
                className="mt-4 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700"
              >
                Scan Ulang
              </button>
            </div>
          )}

          {/* Status message for scanning */}
          {status === 'scanning' && (
            <p className="text-center text-gray-600 text-sm">{message}</p>
          )}

          {/* Manual input fallback */}
          {status === 'scanning' && (
            <div className="mt-4 pt-4 border-t">
              <p className="text-xs text-gray-500 text-center mb-2">
                Tidak bisa scan? Masukkan kode token:
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Token atau URL QR"
                  className="flex-1 px-3 py-2 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleManualInput((e.target as HTMLInputElement).value);
                    }
                  }}
                />
                <button
                  onClick={(e) => {
                    const input = e.currentTarget.previousElementSibling as HTMLInputElement;
                    handleManualInput(input.value);
                  }}
                  className="px-4 py-2 bg-purple-600 text-white text-sm rounded-lg hover:bg-purple-700"
                >
                  OK
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default QRScanner;

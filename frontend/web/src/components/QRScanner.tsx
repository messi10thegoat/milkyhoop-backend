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
      setMessage('Find a QR code to Scan');

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

  // Retry scanning
  const handleRetry = () => {
    setStatus('scanning');
    setMessage('Find a QR code to Scan');
    setApprovalData(null);
    setCameraError(null);
    startQRScanning();
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
    <div className="fixed inset-0 z-50 bg-black">
      {/* Camera as fullscreen background */}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="absolute inset-0 w-full h-full object-cover"
      />

      {/* Close button - glassmorphism circle */}
      <button
        onClick={onClose}
        className="absolute top-4 right-4 z-20 w-11 h-11 rounded-full
                   bg-neutral-900/60 backdrop-blur-md
                   flex items-center justify-center
                   active:bg-neutral-800/60 transition-colors"
      >
        <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>

      {/* Scanning frame - center (only show when scanning) */}
      {status === 'scanning' && (
        <div className="absolute inset-0 flex items-center justify-center z-10">
          <div
            className="w-64 h-64 relative"
            style={{ animation: 'scan-breathe 1s ease-in-out infinite' }}
          >
            {/* Top-left corner */}
            <div className="absolute top-0 left-0 w-12 h-12 border-t-4 border-l-4 border-white rounded-tl-2xl" />
            {/* Top-right corner */}
            <div className="absolute top-0 right-0 w-12 h-12 border-t-4 border-r-4 border-white rounded-tr-2xl" />
            {/* Bottom-left corner */}
            <div className="absolute bottom-0 left-0 w-12 h-12 border-b-4 border-l-4 border-white rounded-bl-2xl" />
            {/* Bottom-right corner */}
            <div className="absolute bottom-0 right-0 w-12 h-12 border-b-4 border-r-4 border-white rounded-br-2xl" />
          </div>
        </div>
      )}

      {/* Keyframes for breathing animation */}
      <style>{`
        @keyframes scan-breathe {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.08); }
        }
      `}</style>

      {/* Nav button - glassmorphism pill (only show when scanning) */}
      {status === 'scanning' && (
        <div className="absolute bottom-8 inset-x-4 flex justify-center z-10">
          <div className="px-6 py-4 rounded-full bg-neutral-900/60 backdrop-blur-md">
            <span className="text-white font-medium">Find a QR code to Scan</span>
          </div>
        </div>
      )}

      {/* Camera error overlay */}
      {cameraError && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/80">
          <div className="text-center px-8">
            <div className="w-20 h-20 bg-red-500/20 rounded-full flex items-center justify-center mx-auto mb-6">
              <svg className="w-10 h-10 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            </div>
            <p className="text-white font-medium text-lg mb-2">{cameraError}</p>
            <p className="text-neutral-400 text-sm mb-6">Pastikan browser memiliki izin kamera</p>
            <button
              onClick={startCamera}
              className="px-6 py-3 rounded-full bg-neutral-900/60 backdrop-blur-md text-white font-medium"
            >
              Coba Lagi
            </button>
          </div>
        </div>
      )}

      {/* Approval confirmation overlay */}
      {status === 'scanned' && approvalData && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full mx-4 overflow-hidden">
            <div className="p-6 text-center">
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
                  className="flex-1 px-4 py-3 bg-gray-100 text-gray-700 rounded-xl font-medium active:bg-gray-200"
                >
                  Batal
                </button>
                <button
                  onClick={handleApprove}
                  className="flex-1 px-4 py-3 bg-purple-600 text-white rounded-xl font-medium active:bg-purple-700"
                >
                  Setuju
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Approving status overlay */}
      {status === 'approving' && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="text-center">
            <div className="animate-spin w-12 h-12 border-4 border-white/30 border-t-white rounded-full mx-auto mb-4" />
            <p className="text-white font-medium">{message}</p>
          </div>
        </div>
      )}

      {/* Success status overlay */}
      {status === 'approved' && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="text-center">
            <div className="w-20 h-20 bg-green-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-10 h-10 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-white font-medium text-lg">{message}</p>
          </div>
        </div>
      )}

      {/* Rejected status overlay */}
      {status === 'rejected' && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="text-center">
            <div className="w-20 h-20 bg-neutral-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-10 h-10 text-neutral-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <p className="text-white font-medium text-lg">{message}</p>
          </div>
        </div>
      )}

      {/* Error status overlay (non-camera errors) */}
      {status === 'error' && !cameraError && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="text-center px-8">
            <div className="w-20 h-20 bg-red-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-10 h-10 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-white font-medium text-lg mb-6">{message}</p>
            <button
              onClick={handleRetry}
              className="px-6 py-3 rounded-full bg-neutral-900/60 backdrop-blur-md text-white font-medium"
            >
              Scan Ulang
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default QRScanner;

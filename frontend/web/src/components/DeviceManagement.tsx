import React, { useState, useEffect, useCallback } from 'react';
import { getToken } from '../utils/auth';
import QRScanner from './QRScanner';

interface Device {
  id: string;
  device_type: 'mobile' | 'web';
  device_name: string | null;
  is_active: boolean;
  is_primary: boolean;
  is_current: boolean;
  last_active_at: string;
  created_at: string;
}

interface DeviceManagementProps {
  isOpen: boolean;
  onClose: () => void;
}

const DeviceManagement: React.FC<DeviceManagementProps> = ({ isOpen, onClose }) => {
  const [devices, setDevices] = useState<Device[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showScanner, setShowScanner] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Fetch devices
  const fetchDevices = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const token = getToken();
      const response = await fetch('/api/devices', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (!response.ok) {
        throw new Error('Failed to fetch devices');
      }

      const data = await response.json();
      if (data.success) {
        setDevices(data.devices);
      } else {
        throw new Error(data.detail || 'Failed to fetch devices');
      }
    } catch (err: any) {
      console.error('Fetch devices error:', err);
      setError(err.message || 'Gagal memuat daftar perangkat');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Logout specific device
  const handleLogoutDevice = async (deviceId: string) => {
    if (!window.confirm('Logout perangkat ini?')) return;

    setActionLoading(deviceId);

    try {
      const token = getToken();
      const response = await fetch(`/api/devices/${deviceId}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (response.ok) {
        // Refresh device list
        await fetchDevices();
      } else {
        const data = await response.json();
        alert(data.detail || 'Gagal logout perangkat');
      }
    } catch (err) {
      console.error('Logout device error:', err);
      alert('Terjadi kesalahan');
    } finally {
      setActionLoading(null);
    }
  };

  // Logout all web devices
  const handleLogoutAllWeb = async () => {
    if (!window.confirm('Logout semua perangkat web? Semua sesi desktop akan diakhiri.')) return;

    setActionLoading('all-web');

    try {
      const token = getToken();
      const response = await fetch('/api/devices/logout-all-web', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      const data = await response.json();

      if (response.ok && data.success) {
        await fetchDevices();
        alert(`${data.count} perangkat web berhasil di-logout`);
      } else {
        alert(data.detail || 'Gagal logout perangkat web');
      }
    } catch (err) {
      console.error('Logout all web error:', err);
      alert('Terjadi kesalahan');
    } finally {
      setActionLoading(null);
    }
  };

  // Format relative time
  const formatRelativeTime = (dateString: string): string => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Baru saja';
    if (diffMins < 60) return `${diffMins} menit lalu`;
    if (diffHours < 24) return `${diffHours} jam lalu`;
    if (diffDays < 7) return `${diffDays} hari lalu`;
    return date.toLocaleDateString('id-ID');
  };

  // Fetch devices on open
  useEffect(() => {
    if (isOpen) {
      fetchDevices();
    }
  }, [isOpen, fetchDevices]);

  if (!isOpen) return null;

  const mobileDevices = devices.filter(d => d.device_type === 'mobile');
  const webDevices = devices.filter(d => d.device_type === 'web');

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-xl max-w-md w-full max-h-[90vh] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b flex-shrink-0">
            <h2 className="text-lg font-bold text-gray-900">Perangkat Terhubung</h2>
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
          <div className="flex-1 overflow-y-auto p-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin w-8 h-8 border-4 border-purple-200 border-t-purple-600 rounded-full" />
              </div>
            ) : error ? (
              <div className="text-center py-8">
                <p className="text-red-600 mb-4">{error}</p>
                <button
                  onClick={fetchDevices}
                  className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700"
                >
                  Coba Lagi
                </button>
              </div>
            ) : (
              <>
                {/* Link new device button */}
                <button
                  onClick={() => setShowScanner(true)}
                  className="w-full flex items-center justify-center gap-2 p-4 bg-purple-50 text-purple-600 rounded-xl hover:bg-purple-100 transition-colors mb-6"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  <span className="font-medium">Hubungkan Perangkat Baru</span>
                </button>

                {/* Mobile devices */}
                <div className="mb-6">
                  <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
                    Perangkat Utama
                  </h3>
                  {mobileDevices.length === 0 ? (
                    <p className="text-gray-500 text-sm py-4 text-center">Tidak ada perangkat mobile</p>
                  ) : (
                    <div className="space-y-2">
                      {mobileDevices.map(device => (
                        <div
                          key={device.id}
                          className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                        >
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
                              <svg className="w-5 h-5 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
                              </svg>
                            </div>
                            <div>
                              <p className="font-medium text-gray-900">
                                {device.device_name || 'Mobile'}
                                {device.is_current && (
                                  <span className="ml-2 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                                    Perangkat ini
                                  </span>
                                )}
                              </p>
                              <p className="text-sm text-gray-500">
                                Aktif {formatRelativeTime(device.last_active_at)}
                              </p>
                            </div>
                          </div>
                          {device.is_primary && (
                            <span className="text-xs text-purple-600 bg-purple-50 px-2 py-1 rounded">
                              Primary
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Web devices */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide">
                      Perangkat Web ({webDevices.length})
                    </h3>
                    {webDevices.length > 0 && (
                      <button
                        onClick={handleLogoutAllWeb}
                        disabled={actionLoading === 'all-web'}
                        className="text-sm text-red-600 hover:text-red-700 font-medium disabled:opacity-50"
                      >
                        {actionLoading === 'all-web' ? 'Loading...' : 'Logout Semua'}
                      </button>
                    )}
                  </div>

                  {webDevices.length === 0 ? (
                    <div className="text-center py-8 bg-gray-50 rounded-lg">
                      <div className="w-12 h-12 bg-gray-200 rounded-full flex items-center justify-center mx-auto mb-3">
                        <svg className="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                        </svg>
                      </div>
                      <p className="text-gray-500 text-sm">Tidak ada sesi web aktif</p>
                      <p className="text-gray-400 text-xs mt-1">
                        Scan QR di desktop untuk menghubungkan
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {webDevices.map(device => (
                        <div
                          key={device.id}
                          className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                        >
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                              <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                              </svg>
                            </div>
                            <div>
                              <p className="font-medium text-gray-900">
                                {device.device_name || 'Web Browser'}
                              </p>
                              <p className="text-sm text-gray-500">
                                Aktif {formatRelativeTime(device.last_active_at)}
                              </p>
                            </div>
                          </div>
                          <button
                            onClick={() => handleLogoutDevice(device.id)}
                            disabled={actionLoading === device.id}
                            className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50"
                          >
                            {actionLoading === device.id ? (
                              <div className="animate-spin w-5 h-5 border-2 border-red-200 border-t-red-600 rounded-full" />
                            ) : (
                              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                              </svg>
                            )}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Footer */}
          <div className="p-4 border-t bg-gray-50 flex-shrink-0">
            <p className="text-xs text-gray-500 text-center">
              Perangkat web akan otomatis logout setelah 30 hari tidak aktif
            </p>
          </div>
        </div>
      </div>

      {/* QR Scanner Modal */}
      <QRScanner
        isOpen={showScanner}
        onClose={() => setShowScanner(false)}
        onScanSuccess={() => {
          setShowScanner(false);
          fetchDevices();
        }}
      />
    </>
  );
};

export default DeviceManagement;

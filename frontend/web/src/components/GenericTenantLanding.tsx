import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

// API base URL from environment variable
const API_BASE_URL = process.env.REACT_APP_API_URL || '';

// Build full URL helper (same logic as api.ts)
const buildUrl = (url: string): string => {
  if (url.startsWith('http')) return url;
  if (API_BASE_URL) return `${API_BASE_URL}${url}`;
  return url;
};

interface TenantInfo {
  tenant_id: string;
  alias: string;
  display_name: string;
  menu_items: any[];
  status: string;
}

const GenericTenantLanding = () => {
  const navigate = useNavigate();
  const { tenantId } = useParams<{ tenantId: string }>();
  const [message, setMessage] = useState('');
  const [tenantInfo, setTenantInfo] = useState<TenantInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchTenantInfo = async () => {
      if (!tenantId) return;
      try {
        const endpoint = `/api/tenant/${tenantId}/info`;
        const fullUrl = buildUrl(endpoint);
        const response = await fetch(fullUrl);
        if (!response.ok) {
          throw new Error('Tenant not found');
        }
        const data = await response.json();
        
        // Parse menu_items if it's a string
        let menuItems = data.data.menu_items;
        if (typeof menuItems === 'string') {
          try {
            menuItems = JSON.parse(menuItems);
          } catch (e) {
            menuItems = [];
          }
        }
        
        setTenantInfo({
          ...data.data,
          menu_items: Array.isArray(menuItems) ? menuItems : []
        });
      } catch (err) {
        setError('Tenant not found');
      } finally {
        setLoading(false);
      }
    };

    fetchTenantInfo();
  }, [tenantId]);

  const handleSendMessage = (messageText: string) => {
    if (!messageText.trim()) return;
    
    // Navigate to chat with initial message
    navigate(`/${tenantId}/chat`, { 
      state: { initialMessage: messageText.trim() }
    });
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage(message);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-purple-600 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-gray-600">Loading...</p>
        </div>
      </div>
    );
  }

  if (error || !tenantInfo) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-4xl font-bold text-gray-800 mb-4">404</h1>
          <p className="text-gray-600 mb-4">Tenant not found</p>
          <button 
            onClick={() => navigate('/')}
            className="px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700"
          >
            Go Home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <button 
            onClick={() => navigate('/')}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex items-center space-x-2">
            <h1 className="text-xl font-bold">{tenantInfo.display_name}</h1>
            <div className="w-8 h-8 bg-purple-400 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">
                {tenantInfo.display_name.substring(0, 2).toUpperCase()}
              </span>
            </div>
          </div>
          <div className="w-6"></div>
        </div>
      </header>

      {/* Main Content */}
      <div className="max-w-4xl mx-auto px-4 py-6 pb-24">
        {/* Brand Section */}
        <div className="bg-white rounded-lg shadow-sm p-6 mb-6">
          <div className="flex items-center space-x-4 mb-6">
            <div className="w-16 h-16 bg-purple-600 rounded-xl flex items-center justify-center">
              <div className="w-10 h-10 bg-white rounded-lg flex items-center justify-center">
                <div className="text-purple-600 font-bold text-sm">
                  {tenantInfo.display_name.substring(0, 2).toUpperCase()}
                </div>
              </div>
            </div>
            <div>
              <h2 className="text-2xl font-bold text-gray-900">{tenantInfo.display_name}</h2>
            </div>
          </div>

          {/* Hero Image */}
          <div className="relative mb-6">
            <div className="bg-gradient-to-r from-purple-600 to-purple-800 rounded-lg p-8 text-white">
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <h3 className="text-xl font-semibold mb-2">Welcome to {tenantInfo.display_name}</h3>
                  <p className="text-purple-100">Chat with us to get started</p>
                </div>
                <div className="w-32 h-20 bg-white/20 rounded-lg flex items-center justify-center">
                  <div className="text-center">
                    <div className="w-16 h-10 bg-white/30 rounded mb-2"></div>
                    <div className="text-xs">Chat Now</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Product Grid */}
        {tenantInfo.menu_items && tenantInfo.menu_items.length > 0 ? (
          <div className="grid grid-cols-2 gap-4 mb-8">
            {tenantInfo.menu_items.map((item: any, index: number) => (
              <div key={index} className="bg-white rounded-lg shadow-sm p-4">
                <div className="w-full h-24 bg-gradient-to-br from-purple-500 to-purple-700 rounded-lg mb-3 flex items-center justify-center">
                  <div className="text-white text-sm font-medium text-center">
                    {item.name || item.service || 'Item'}
                  </div>
                </div>
                <p className="text-sm text-gray-600">
                  {item.price ? `Rp ${item.price.toLocaleString()}` : item.desc || 'Available'}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 mb-8">
            {[
              { title: "Product 1", desc: "Available" },
              { title: "Product 2", desc: "Available" },
              { title: "Product 3", desc: "Available" },
              { title: "Product 4", desc: "Available" }
            ].map((product, index) => (
              <div key={index} className="bg-white rounded-lg shadow-sm p-4">
                <div className="w-full h-24 bg-gradient-to-br from-purple-500 to-purple-700 rounded-lg mb-3 flex items-center justify-center">
                  <div className="text-white text-sm font-medium text-center">
                    {product.title}
                  </div>
                </div>
                <p className="text-sm text-gray-600">{product.desc}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Fixed Bottom Chat Input */}
      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 p-4 shadow-lg">
        <div className="max-w-4xl mx-auto">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 bg-purple-400 rounded-full flex items-center justify-center flex-shrink-0">
              <span className="text-white font-bold text-sm">m</span>
            </div>
            <div className="flex-1 relative">
              <input
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder={`Chat dengan ${tenantInfo.display_name}...`}
                className="w-full px-4 py-3 pr-12 border border-gray-300 rounded-full focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
              <button
                onClick={() => handleSendMessage(message)}
                disabled={!message.trim()}
                className="absolute right-2 top-1/2 transform -translate-y-1/2 w-8 h-8 bg-purple-600 text-white rounded-full flex items-center justify-center hover:bg-purple-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default GenericTenantLanding;

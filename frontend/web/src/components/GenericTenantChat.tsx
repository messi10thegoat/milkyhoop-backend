import React, { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation, useParams } from 'react-router-dom';
import { isAuthenticated, getUserEmail, logout } from '../utils/auth';
import { checkTenantAccess, sendTenantMessage } from '../utils/api';
import LoginModal from './LoginModal';

// API base URL from environment variable
const API_BASE_URL = process.env.REACT_APP_API_URL || '';

// Build full URL helper (same logic as api.ts)
const buildUrl = (url: string): string => {
  if (url.startsWith('http')) return url;
  if (API_BASE_URL) return `${API_BASE_URL}${url}`;
  return url;
};

interface Message {
  id: string;
  text: string;
  isUser: boolean;
  timestamp: Date;
}

const GenericTenantChat = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { tenantId } = useParams<{ tenantId: string }>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isTenantMode, setIsTenantMode] = useState(false);
  const [isCheckingAccess, setIsCheckingAccess] = useState(true);
  const [showLoginModal, setShowLoginModal] = useState(false);
  const [tenantInfo, setTenantInfo] = useState<any>(null);
  const [isUserScrolling, setIsUserScrolling] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  // UX IMPROVEMENT #4: Smart scroll - only auto-scroll when user is at bottom
  const isAtBottom = () => {
    const container = messagesContainerRef.current;
    if (!container) return true;
    const threshold = 50; // 50px tolerance
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
  };

  const scrollToBottom = (force = false) => {
    if (force || !isUserScrolling || isAtBottom()) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  };

  // UX IMPROVEMENT #1: Disable textarea during loading
  const adjustTextareaHeight = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    
    textarea.style.height = 'auto';
    const newHeight = Math.min(Math.max(textarea.scrollHeight, 48), 200);
    textarea.style.height = `${newHeight}px`;
  };

  // UX IMPROVEMENT #3: Relative timestamp helper
  const getRelativeTime = (timestamp: Date) => {
    const now = new Date();
    const diffInSeconds = Math.floor((now.getTime() - timestamp.getTime()) / 1000);
    
    if (diffInSeconds < 60) return 'Baru saja';
    if (diffInSeconds < 3600) return `${Math.floor(diffInSeconds / 60)} menit lalu`;
    if (diffInSeconds < 86400) return `${Math.floor(diffInSeconds / 3600)} jam lalu`;
    
    // Show date if different day
    const isSameDay = now.toDateString() === timestamp.toDateString();
    if (!isSameDay) {
      return timestamp.toLocaleDateString('id-ID', { day: 'numeric', month: 'short' });
    }
    
    return timestamp.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
  };

  // UX IMPROVEMENT #5: Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Esc to clear textarea
      if (e.key === 'Escape' && !isLoading) {
        setInputMessage('');
        textareaRef.current?.blur();
      }
      
      // Ctrl+K to focus input
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        textareaRef.current?.focus();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isLoading]);

  // Track user scroll behavior for smart scroll
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      setIsUserScrolling(!isAtBottom());
    };

    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    adjustTextareaHeight();
  }, [inputMessage]);

  // Fetch tenant info
  useEffect(() => {
    const fetchTenantInfo = async () => {
      if (!tenantId) return;
      try {
        const endpoint = `/api/tenant/${tenantId}/info`;
        const fullUrl = buildUrl(endpoint);
        const response = await fetch(fullUrl);
        if (response.ok) {
          const data = await response.json();
          setTenantInfo(data.data);
        }
      } catch (err) {
        console.error('Failed to fetch tenant info:', err);
      }
    };
    fetchTenantInfo();
  }, [tenantId]);

  // Check tenant access on mount
  useEffect(() => {
    const checkAccess = async () => {
      if (!tenantId) {
        setIsCheckingAccess(false);
        return;
      }
      
      if (isAuthenticated()) {
        try {
          const hasAccess = await checkTenantAccess(tenantId);
          setIsTenantMode(hasAccess);
        } catch (error) {
          console.error('Access check failed:', error);
          setIsTenantMode(false);
        }
      } else {
        setIsTenantMode(false);
      }
      setIsCheckingAccess(false);
    };
    
    checkAccess();
  }, [tenantId]);

  // Handle initial message from landing page
  useEffect(() => {
    const initialMessage = location.state?.initialMessage;
    if (initialMessage && !isCheckingAccess) {
      sendMessage(initialMessage);
    }
  }, [location.state, isCheckingAccess]);

  // Listen for logout events
  useEffect(() => {
    const handleLogoutEvent = (event: any) => {
      const reason = event.detail?.reason || 'unknown';
      console.log(`[GenericTenantChat] Logout detected, reason: ${reason}`);
      
      setIsTenantMode(false);
      setShowLoginModal(false);
      
      // Show notification for non-manual logouts
      if (reason !== 'manual') {
        const messages = {
          'token_expired': 'Session expired. Please login again.',
          'refresh_failed': 'Session expired. Please login again.',
          'network_error': 'Connection lost. Please check your internet and login again.',
          'no_refresh_token': 'Session expired. Please login again.'
        };
        alert(messages[reason as keyof typeof messages] || 'Session expired. Please login again.');
      }
    };

    window.addEventListener('logout', handleLogoutEvent);
    return () => window.removeEventListener('logout', handleLogoutEvent);
  }, []);

  const handleLogout = () => {
    logout('manual');
  };

  const sendMessage = async (messageText: string) => {
    if (!messageText.trim() || isLoading || !tenantId) return;

    // UX IMPROVEMENT #6: Haptic feedback on send (mobile only)
    if ('vibrate' in navigator) {
      navigator.vibrate(50);
    }

    const userMessage: Message = {
      id: Date.now().toString(),
      text: messageText,
      isUser: true,
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    setInputMessage('');
    setTimeout(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }, 0);
    setIsLoading(true);

    // Force scroll to bottom when sending new message
    scrollToBottom(true);

    try {
      let response;
      
      if (isTenantMode) {
        // Use tenant mode API with auth
        response = await sendTenantMessage(tenantId, messageText);
      } else {
        // Use customer mode API (public)
        const endpoint = `/tenant/${tenantId}/chat`;
        const fullUrl = buildUrl(endpoint);
        const res = await fetch(fullUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: messageText })
        });
        response = await res.json();
      }

      const botMessage: Message = {
        id: (Date.now() + 1).toString(),
        text: response.milky_response || response.message || 'Maaf, terjadi kesalahan.',
        isUser: false,
        timestamp: new Date()
      };

      setMessages(prev => [...prev, botMessage]);
    } catch (error) {
      console.error('Error sending message:', error);
      const errorMessage: Message = {
        id: (Date.now() + 1).toString(),
        text: 'Maaf, terjadi kesalahan. Silakan coba lagi.',
        isUser: false,
        timestamp: new Date()
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(inputMessage);
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = 'auto';
        }
      }, 0);
    }
  };

  if (isCheckingAccess) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-purple-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <button 
            onClick={() => navigate(`/${tenantId}`)}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex items-center space-x-2">
            <h1 className="text-xl font-bold">{tenantInfo?.display_name || 'Chat'}</h1>
            {isTenantMode && (
              <span className="px-2 py-1 bg-purple-100 text-purple-700 text-xs font-semibold rounded-full">
                Tenant Mode
              </span>
            )}
          </div>
          {isTenantMode ? (
            <button 
              onClick={handleLogout} 
              className="px-4 py-2 bg-gray-600 text-white rounded-lg text-sm font-medium hover:bg-gray-700 min-w-[44px] min-h-[44px]"
            >
              Logout
            </button>
          ) : (
            <button 
              onClick={() => setShowLoginModal(true)} 
              className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 min-w-[44px] min-h-[44px]"
            >
              Login
            </button>
          )}
        </div>
      </header>

      {/* Tenant Mode Quick Actions */}
      {isTenantMode && (
        <div className="bg-purple-50 border-b border-purple-100 px-4 py-3">
          <div className="max-w-4xl mx-auto flex items-center space-x-2 overflow-x-auto">
            <button
              onClick={() => sendMessage("produk apa yang paling laku?")}
              disabled={isLoading}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              📊 Top Products
            </button>
            <button
              onClick={() => sendMessage("produk mana yang kurang laku?")}
              disabled={isLoading}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              ⚠️ Low Sellers
            </button>
            <button
              onClick={() => sendMessage("cek stok ballpoint")}
              disabled={isLoading}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              📦 Check Stock
            </button>
            <button
              onClick={() => sendMessage("untung bulan ini berapa?")}
              disabled={isLoading}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              💰 Monthly Profit
            </button>
          </div>
        </div>
      )}

      {/* Messages */}
      <div 
        ref={messagesContainerRef}
        className="flex-1 overflow-y-auto px-4 py-6"
      >
        <div className="max-w-4xl mx-auto space-y-4">
          {messages.map((message) => (
            <div
              key={message.id}
              className={`flex ${message.isUser ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                  message.isUser
                    ? 'bg-purple-600 text-white'
                    : 'bg-white text-gray-800 shadow-sm'
                }`}
              >
                <p className="text-sm whitespace-pre-wrap text-left font-mono">{message.text}</p>
                <p className={`text-xs mt-1 ${message.isUser ? 'text-purple-200' : 'text-gray-400'}`}>
                  {getRelativeTime(message.timestamp)}
                </p>
              </div>
            </div>
          ))}
          
          {isLoading && (
            <div className="flex justify-start">
              <div className="bg-white rounded-2xl px-4 py-3 shadow-sm">
                <div className="flex space-x-2">
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></div>
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></div>
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></div>
                </div>
              </div>
            </div>
          )}
          
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Chat Input */}
      <div className="bg-white border-t p-4">
        <div className="max-w-4xl mx-auto flex items-end space-x-3">
          <div className="flex-1">
            <textarea
              ref={textareaRef}
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ketik pesan Anda... (Esc untuk clear, Ctrl+K untuk focus)"
              className={`w-full px-4 py-3 border border-gray-300 rounded-2xl resize-none focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent overflow-y-auto transition-opacity ${
                isLoading ? 'opacity-50 cursor-not-allowed' : 'opacity-100'
              }`}
              style={{ minHeight: '48px', maxHeight: '200px' }}
              rows={1}
              disabled={isLoading}
            />
          </div>
          <button
            onClick={() => sendMessage(inputMessage)}
            disabled={!inputMessage.trim() || isLoading}
            className={`min-w-[48px] min-h-[48px] rounded-full flex items-center justify-center transition-all duration-200 ${
              inputMessage.trim() && !isLoading
                ? 'bg-purple-600 hover:bg-purple-700 text-white scale-100'
                : 'bg-gray-300 text-gray-500 cursor-not-allowed scale-95'
            }`}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
      </div>

      {/* Login Modal */}
      <LoginModal
        isOpen={showLoginModal}
        onClose={() => setShowLoginModal(false)}
        onLoginSuccess={async () => {
          setShowLoginModal(false);
          setIsCheckingAccess(true);
          if (tenantId) {
            try {
              const hasAccess = await checkTenantAccess(tenantId);
              setIsTenantMode(hasAccess);
            } catch (error) {
              console.error('Access check failed:', error);
              setIsTenantMode(false);
            }
          }
          setIsCheckingAccess(false);
        }}
      />
    </div>
  );
};

export default GenericTenantChat;
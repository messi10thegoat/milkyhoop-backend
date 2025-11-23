import React, { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { isAuthenticated, getUserEmail, logout } from '../utils/auth';
import { checkTenantAccess, sendTenantMessage } from '../utils/api';
import LoginModal from './LoginModal';

interface Message {
  id: string;
  text: string;
  isUser: boolean;
  timestamp: Date;
}

const KonsultanPsikologiChat = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isTenantMode, setIsTenantMode] = useState(false);
  const [isCheckingAccess, setIsCheckingAccess] = useState(true);
  const [showLoginModal, setShowLoginModal] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Check tenant access on mount
  useEffect(() => {
    const checkAccess = async () => {
      if (isAuthenticated()) {
        const hasAccess = await checkTenantAccess('konsultanpsikologi');
        setIsTenantMode(hasAccess);
      } else {
        setIsTenantMode(false);
      }
      setIsCheckingAccess(false);
    };
    
    checkAccess();
  }, []);

  // Handle initial message from landing page
  useEffect(() => {
    const initialMessage = location.state?.initialMessage;
    if (initialMessage && !isCheckingAccess) {
      sendMessage(initialMessage);
    }
  }, [location.state, isCheckingAccess]);

  const handleLogout = () => {
    logout();
    window.location.reload();
  };

  const sendMessage = async (messageText: string) => {
    if (!messageText.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      text: messageText,
      isUser: true,
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    setInputMessage('');
    setIsLoading(true);

    try {
      let response;
      
      if (isTenantMode) {
        // Use tenant mode API with auth
        response = await sendTenantMessage('konsultanpsikologi', messageText);
      } else {
        // Use customer mode API (public)
        const res = await fetch('/tenant/konsultanpsikologi/chat', {
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
            onClick={() => navigate('/konsultanpsikologi')}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex items-center space-x-2">
            <h1 className="text-xl font-bold">Konsultan Psikologi</h1>
            {isTenantMode && (
              <span className="px-2 py-1 bg-purple-100 text-purple-700 text-xs font-semibold rounded-full">
                Tenant Mode
              </span>
            )}
          </div>
          {isTenantMode ? (
            <button 
              onClick={handleLogout} 
              className="px-4 py-2 bg-gray-600 text-white rounded-lg text-sm font-medium hover:bg-gray-700"
            >
              Logout
            </button>
          ) : (
            <button 
              onClick={() => setShowLoginModal(true)} 
              className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700"
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
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap"
            >
              📊 Top Products
            </button>
            <button
              onClick={() => sendMessage("produk mana yang kurang laku?")}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap"
            >
              ⚠️ Low Sellers
            </button>
            <button
              onClick={() => sendMessage("cek stok ballpoint")}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap"
            >
              📦 Check Stock
            </button>
            <button
              onClick={() => sendMessage("untung bulan ini berapa?")}
              className="px-4 py-2 bg-white border border-purple-200 rounded-lg text-sm font-medium text-purple-700 hover:bg-purple-50 whitespace-nowrap"
            >
              💰 Monthly Profit
            </button>
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
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
                <p className="text-sm whitespace-pre-wrap">{message.text}</p>
                <p className={`text-xs mt-1 ${message.isUser ? 'text-purple-200' : 'text-gray-400'}`}>
                  {message.timestamp.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })}
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
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ketik pesan Anda..."
              className="w-full px-4 py-3 border border-gray-300 rounded-2xl resize-none focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              rows={1}
              disabled={isLoading}
            />
          </div>
          <button
            onClick={() => sendMessage(inputMessage)}
            disabled={!inputMessage.trim() || isLoading}
            className="w-12 h-12 bg-purple-400 text-white rounded-full flex items-center justify-center hover:bg-purple-500 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
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
          const hasAccess = await checkTenantAccess('konsultanpsikologi');
          setIsTenantMode(hasAccess);
          setIsCheckingAccess(false);
        }}
      />
    </div>
  );
};

export default KonsultanPsikologiChat;
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

const HomePage = () => {
  const navigate = useNavigate();
  const [message, setMessage] = useState('');

  const handleSendMessage = () => {
    if (!message.trim()) return;
    // Navigate to setup mode chat
    navigate('/chat', { 
      state: { initialMessage: message.trim() }
    });
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-white to-gray-50">
      {/* Main Content */}
      <div className="flex items-center justify-center px-4 py-16 pb-32">
        <div className="max-w-2xl w-full text-center">
          {/* Logo/Icon */}
          <div className="mb-8">
            <div className="w-20 h-20 bg-gradient-to-br from-purple-500 to-purple-700 rounded-3xl mx-auto flex items-center justify-center shadow-lg">
              <span className="text-white text-4xl font-bold">m</span>
            </div>
          </div>

          {/* Hero Text */}
          <h1 className="text-5xl font-semibold text-gray-900 mb-4 tracking-tight">
            Hai, Milky di sini 😊
          </h1>
          
          <p className="text-xl text-gray-600 mb-12 font-light leading-relaxed">
            Chat aja, Milky bantu kamu<br />
            catat keuangan secara otomatis
          </p>

          {/* Features - Minimal */}
          <div className="flex justify-center items-center space-x-12 text-gray-500 text-sm mb-8">
            <div className="flex flex-col items-center">
              <div className="text-2xl mb-1">💬</div>
              <span className="font-medium">Simple</span>
            </div>
            <div className="flex flex-col items-center">
              <div className="text-2xl mb-1">🎯</div>
              <span className="font-medium">Smart</span>
            </div>
            <div className="flex flex-col items-center">
              <div className="text-2xl mb-1">✨</div>
              <span className="font-medium">Auto</span>
            </div>
          </div>

          {/* Scroll hint */}
          <div className="text-sm text-gray-400 animate-bounce">
            ↓ Chat di bawah untuk mulai
          </div>
        </div>
      </div>

      {/* Sticky Footer Input */}
      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 p-4 shadow-lg">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 bg-purple-400 rounded-full flex items-center justify-center flex-shrink-0">
              <span className="text-white font-bold text-sm">m</span>
            </div>
            <div className="flex-1 relative">
              <input
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Tanya Milky tentang fitur, harga, atau cara pakai..."
                className="w-full px-4 py-3 pr-12 border border-gray-300 rounded-full focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
              <button
                onClick={handleSendMessage}
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

export default HomePage;
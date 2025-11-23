import React from 'react';
import { useNavigate } from 'react-router-dom';

const KonsultanPsikologiLanding = () => {
  const navigate = useNavigate();
  const [message, setMessage] = React.useState('');

  const handleSendMessage = (messageText: string) => {
    if (!messageText.trim()) return;
    
    // Navigate to chat with initial message
    navigate('/konsultanpsikologi/chat', { 
      state: { initialMessage: messageText.trim() }
    });
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage(message);
    }
  };

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
            <h1 className="text-xl font-bold">Konsultan Psikologi</h1>
            <div className="w-8 h-8 bg-purple-400 rounded-lg flex items-center justify-center">
              <svg className="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z" />
              </svg>
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
                <div className="text-purple-600 font-bold text-sm">KP</div>
              </div>
            </div>
            <div>
              <h2 className="text-2xl font-bold text-gray-900">Konsultan Psikologi</h2>
            </div>
          </div>

          {/* Hero Image */}
          <div className="relative mb-6">
            <div className="bg-gradient-to-r from-purple-600 to-purple-800 rounded-lg p-8 text-white">
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <h3 className="text-xl font-semibold mb-2">Layanan Konseling Profesional</h3>
                  <p className="text-purple-100">Solusi kesehatan mental terpercaya</p>
                </div>
                <div className="w-32 h-20 bg-white/20 rounded-lg flex items-center justify-center">
                  <div className="text-center">
                    <div className="w-16 h-10 bg-white/30 rounded mb-2"></div>
                    <div className="text-xs">Konseling</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Product Grid */}
        <div className="grid grid-cols-2 gap-4 mb-8">
          {[
            { title: "Konseling Individu", desc: "Rp 150.000/sesi" },
            { title: "Konseling Pasangan", desc: "Rp 250.000/sesi" },
            { title: "Konseling Keluarga", desc: "Rp 300.000/sesi" },
            { title: "Konseling Online", desc: "Fleksibel" }
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
                placeholder="Chat dengan konsultan..."
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

export default KonsultanPsikologiLanding;

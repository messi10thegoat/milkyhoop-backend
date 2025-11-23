import React from 'react';
import { useNavigate } from 'react-router-dom';

const BCALanding = () => {
  const navigate = useNavigate();

  const handleChatClick = () => {
    navigate('/bca/chat');
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <button className="p-2">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex items-center space-x-2">
            <h1 className="text-xl font-bold">BCA</h1>
            <div className="w-8 h-8 bg-yellow-400 rounded-lg flex items-center justify-center">
              <svg className="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
              </svg>
            </div>
          </div>
          <div className="w-6"></div>
        </div>
      </header>

      {/* Main Content */}
      <div className="max-w-4xl mx-auto px-4 py-6">
        {/* Brand Section */}
        <div className="bg-white rounded-lg shadow-sm p-6 mb-6">
          <div className="flex items-center space-x-4 mb-6">
            <div className="w-16 h-16 bg-blue-600 rounded-xl flex items-center justify-center">
              <div className="w-10 h-10 bg-white rounded-lg flex items-center justify-center">
                <div className="text-blue-600 font-bold text-sm">BCA</div>
              </div>
            </div>
            <div>
              <h2 className="text-2xl font-bold text-gray-900">Bank Central Asia</h2>
            </div>
          </div>

          {/* Hero Image */}
          <div className="relative mb-6">
            <div className="bg-gradient-to-r from-blue-600 to-blue-800 rounded-lg p-8 text-white">
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <h3 className="text-xl font-semibold mb-2">BCA Digital Banking</h3>
                  <p className="text-blue-100">Layanan perbankan digital terpercaya</p>
                </div>
                <div className="w-32 h-20 bg-white/20 rounded-lg flex items-center justify-center">
                  <div className="text-center">
                    <div className="w-16 h-10 bg-white/30 rounded mb-2"></div>
                    <div className="text-xs">BCA Card</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Product Grid */}
        <div className="grid grid-cols-2 gap-4 mb-8">
          {[
            { title: "Paspor Blue", desc: "Kartu kredit premium" },
            { title: "TabunganKu", desc: "Tabungan mudah" },
            { title: "Tahapan Xpresi", desc: "Tabungan berbunga" },
            { title: "BCA Mobile", desc: "Banking digital" }
          ].map((product, index) => (
            <div key={index} className="bg-white rounded-lg shadow-sm p-4">
              <div className="w-full h-24 bg-gradient-to-br from-blue-500 to-blue-700 rounded-lg mb-3 flex items-center justify-center">
                <div className="text-white text-sm font-medium text-center">
                  {product.title}
                </div>
              </div>
              <p className="text-sm text-gray-600">{product.desc}</p>
            </div>
          ))}
        </div>

        {/* Chat CTA */}
        <div className="fixed bottom-6 left-1/2 transform -translate-x-1/2 w-full max-w-sm px-4">
          <button
            onClick={handleChatClick}
            className="w-full bg-gray-800 text-white py-4 rounded-xl font-medium shadow-lg hover:bg-gray-900 transition-colors"
          >
            Chat with bca
          </button>
          <div className="absolute -top-4 right-8">
            <div className="w-12 h-12 bg-yellow-400 rounded-full flex items-center justify-center shadow-lg">
              <span className="text-white font-bold text-lg">m</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default BCALanding;

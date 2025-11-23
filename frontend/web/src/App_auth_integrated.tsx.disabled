import React from 'react';
import { BrowserRouter as Router, Routes, Route, useNavigate, useLocation } from 'react-router-dom';

// Import existing components (preserve current functionality)
import HomePage from './components/HomePage';
import BCALanding from './components/BCALanding';
import BCAChat from './components/BCAChat';

// Import new auth components
import { AuthProvider } from './contexts/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import LoginForm from './components/LoginForm';
import RegisterForm from './components/RegisterForm';
import UserMenu from './components/UserMenu';

// Import TenantLanding for multi-tenant support
import TenantLanding from './pages/TenantLanding';

function App() {
  return (
    <AuthProvider>
      <Router>
        <div className="App">
          <Routes>
            {/* Public routes - existing functionality preserved */}
            <Route path="/" element={<HomePage />} />
            <Route path="/bca" element={<BCALanding />} />
            <Route path="/bca/chat" element={<BCAChat />} />
            
            {/* Auth routes - new functionality */}
            <Route path="/login" element={<LoginForm />} />
            <Route path="/register" element={<RegisterForm />} />
            
            {/* Protected setup mode - existing chat with auth */}
            <Route path="/chat" element={
              <ProtectedRoute>
                <SetupModeChat />
              </ProtectedRoute>
            } />
            
            {/* Multi-tenant routes */}
            <Route path="/:tenantId" element={<TenantLanding />} />
            <Route path="/:tenantId/chat" element={<BCAChat />} />
          </Routes>
        </div>
      </Router>
    </AuthProvider>
  );
}

// Setup Mode Chat Component (enhanced with auth context)
const SetupModeChat = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [message, setMessage] = React.useState('');
  const [chat, setChat] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const messagesEndRef = React.useRef(null);

  // Handle initial message from landing page
  React.useEffect(() => {
    const initialMessage = location.state?.initialMessage;
    if (initialMessage && chat.length === 0) {
      sendMessage(initialMessage);
    }
  }, [location.state]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  React.useEffect(() => {
    scrollToBottom();
  }, [chat]);

  const sendMessage = async (messageText = message) => {
    if (!messageText.trim() || loading) return;

    const userMessage = {
      type: 'user',
      content: messageText.trim(),
      timestamp: new Date().toISOString()
    };

    setChat(prevChat => [...prevChat, userMessage]);
    setMessage('');
    setLoading(true);

    try {
      const response = await fetch("/api/chat/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_id: "business_owner_" + Date.now(),
          tenant_id: "konsultanpsikologi",
          message: messageText.trim(),
          session_id: "setup_" + Date.now(),
        })
      });

      if (response.ok) {
        const data = await response.json();
        const milkyResponse = {
          type: 'milky',
          content: data.milky_response || data.response || "I'm here to help setup your chatbot!",
          timestamp: new Date().toISOString()
        };
        setChat(prevChat => [...prevChat, milkyResponse]);
      } else {
        console.error('API Error:', response.status, response.statusText);
        const errorResponse = {
          type: 'error',
          content: "Sorry, I'm having trouble connecting. Please try again.",
          timestamp: new Date().toISOString()
        };
        setChat(prevChat => [...prevChat, errorResponse]);
      }
    } catch (error) {
      console.error('Network Error:', error);
      const errorResponse = {
        type: 'error',
        content: "Network error. Please check your connection and try again.",
        timestamp: new Date().toISOString()
      };
      setChat(prevChat => [...prevChat, errorResponse]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-indigo-50">
      {/* Header with user menu */}
      <div className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-4 flex justify-between items-center">
          <div className="flex items-center">
            <div className="w-10 h-10 bg-gradient-to-r from-blue-600 to-indigo-600 rounded-2xl flex items-center justify-center mr-3">
              <span className="text-white font-bold text-lg">M</span>
            </div>
            <h1 className="text-xl font-semibold text-gray-900">Setup Chat</h1>
          </div>
          <UserMenu />
        </div>
      </div>

      {/* Chat Interface */}
      <div className="max-w-4xl mx-auto p-4">
        <div className="bg-white rounded-2xl shadow-lg border border-gray-100 h-[600px] flex flex-col">
          {/* Chat Messages */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {chat.length === 0 && (
              <div className="text-center text-gray-500 mt-8">
                <div className="w-16 h-16 bg-gradient-to-r from-blue-600 to-indigo-600 rounded-full flex items-center justify-center mx-auto mb-4">
                  <span className="text-white font-bold text-xl">M</span>
                </div>
                <p className="text-lg font-medium">Hi! I'm Milky, your AI setup assistant</p>
                <p className="text-sm">Tell me what you need help with for your chatbot</p>
              </div>
            )}
            
            {chat.map((msg, index) => (
              <div key={index} className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-xs lg:max-w-md px-4 py-2 rounded-lg ${
                  msg.type === 'user' 
                    ? 'bg-blue-600 text-white' 
                    : msg.type === 'error'
                    ? 'bg-red-100 text-red-700 border border-red-200'
                    : 'bg-gray-100 text-gray-900'
                }`}>
                  <p className="text-sm">{msg.content}</p>
                  <p className={`text-xs mt-1 ${msg.type === 'user' ? 'text-blue-100' : 'text-gray-500'}`}>
                    {new Date(msg.timestamp).toLocaleTimeString()}
                  </p>
                </div>
              </div>
            ))}
            
            {loading && (
              <div className="flex justify-start">
                <div className="bg-gray-100 rounded-lg px-4 py-2">
                  <div className="flex space-x-1">
                    <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></div>
                    <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></div>
                    <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></div>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Message Input */}
          <div className="border-t border-gray-100 p-4">
            <div className="flex space-x-3">
              <input
                type="text"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
                placeholder="Type your message..."
                disabled={loading}
                className="flex-1 px-4 py-3 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
              />
              <button
                onClick={() => sendMessage()}
                disabled={loading || !message.trim()}
                className="px-6 py-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                Send
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default App;

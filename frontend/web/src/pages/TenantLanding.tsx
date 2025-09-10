import React from 'react';
import { useParams } from 'react-router-dom';

const TenantLanding = () => {
  const { tenantSlug } = useParams<{ tenantSlug: string }>();

  // BCA is handled by dedicated routes
  if (tenantSlug?.toLowerCase() === 'bca') {
    window.location.href = '/bca';
    return null;
  }

  // Default for unknown tenants
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-gray-900 mb-4">
          {tenantSlug}
        </h1>
        <p className="text-gray-600 mb-8">
          This tenant is not available yet.
        </p>
        <a href="/" className="text-blue-600 hover:text-blue-800">
          ← Back to MilkyHoop Platform
        </a>
      </div>
    </div>
  );
};

export default TenantLanding;

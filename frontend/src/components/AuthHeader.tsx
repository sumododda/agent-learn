'use client';

import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';

export function AuthHeader() {
  const { isSignedIn, isLoaded, logout } = useAuth();

  return (
    <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
      <div className="flex items-center gap-6">
        <Link href="/" className="text-lg font-semibold text-white hover:text-gray-300">
          agent-learn
        </Link>
        <Link href="/library" className="text-sm text-gray-400 hover:text-white transition-colors">
          My Courses
        </Link>
      </div>
      <div className="flex items-center gap-4">
        {isLoaded && !isSignedIn && (
          <Link href="/login" className="text-sm text-gray-400 hover:text-white transition-colors">
            Sign In
          </Link>
        )}
        {isLoaded && isSignedIn && (
          <button
            onClick={logout}
            className="text-sm text-gray-400 hover:text-white transition-colors"
          >
            Sign Out
          </button>
        )}
      </div>
    </header>
  );
}

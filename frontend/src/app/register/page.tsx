'use client';

import { useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { Turnstile, type TurnstileInstance } from '@marsidev/react-turnstile';
import { useAuth } from '@/context/AuthContext';

export default function RegisterPage() {
  const router = useRouter();
  const { register } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const turnstileRef = useRef<TurnstileInstance>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!turnstileToken) {
      setError('Please complete the verification');
      return;
    }
    setLoading(true);
    try {
      await register(email, password, turnstileToken);
      router.push(`/verify?email=${encodeURIComponent(email)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
      turnstileRef.current?.reset();
      setTurnstileToken(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh]">
      <h1 className="text-2xl font-bold mb-6">Create Account</h1>

      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-4">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          required
          className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
          disabled={loading}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          required
          className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-purple-500"
          disabled={loading}
        />
        <Turnstile
          ref={turnstileRef}
          siteKey={process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || '1x00000000000000000000AA'}
          onSuccess={(token) => setTurnstileToken(token)}
          onError={() => setTurnstileToken(null)}
          onExpire={() => {
            setTurnstileToken(null);
            turnstileRef.current?.reset();
          }}
          options={{ theme: 'dark', size: 'flexible' }}
        />
        <button
          type="submit"
          disabled={loading || !turnstileToken}
          className="w-full py-3 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg font-medium transition-colors"
        >
          {loading ? 'Creating account...' : !turnstileToken ? 'Verifying...' : 'Register'}
        </button>
        {error && <p className="text-red-400 text-sm text-center">{error}</p>}
      </form>

      <p className="mt-6 text-gray-400 text-sm">
        Already have an account?{' '}
        <Link href="/login" className="text-purple-400 hover:text-purple-300">
          Sign In
        </Link>
      </p>
    </div>
  );
}

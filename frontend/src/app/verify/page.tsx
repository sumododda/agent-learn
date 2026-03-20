'use client';

import { useState, useRef, useEffect, useCallback, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';

function VerifyForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const email = searchParams.get('email');
  const { verifyOtp, resendOtp } = useAuth();

  const [otp, setOtp] = useState<string[]>(Array(6).fill(''));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [shake, setShake] = useState(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  // Redirect if no email
  useEffect(() => {
    if (!email) {
      router.push('/register');
    }
  }, [email, router]);

  // Resend cooldown timer
  useEffect(() => {
    if (resendCooldown <= 0) return;
    const timer = setTimeout(() => setResendCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendCooldown]);

  const handleSubmit = useCallback(async (code: string) => {
    if (!email || code.length !== 6) return;
    setError(null);
    setLoading(true);
    try {
      await verifyOtp(email, code);
      router.push('/');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Verification failed';
      if (message.includes('expired') || message.includes('not found')) {
        router.push('/register');
        return;
      }
      setError(message);
      setShake(true);
      setTimeout(() => setShake(false), 500);
      setOtp(Array(6).fill(''));
      inputRefs.current[0]?.focus();
    } finally {
      setLoading(false);
    }
  }, [email, verifyOtp, router]);

  function handleChange(index: number, value: string) {
    if (!/^\d?$/.test(value)) return; // only allow single digit
    const newOtp = [...otp];
    newOtp[index] = value;
    setOtp(newOtp);

    if (value && index < 5) {
      inputRefs.current[index + 1]?.focus();
    }

    // Auto-submit when all digits entered
    const code = newOtp.join('');
    if (code.length === 6 && newOtp.every((d) => d !== '')) {
      handleSubmit(code);
    }
  }

  function handleKeyDown(index: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Backspace' && !otp[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6);
    if (pasted.length === 0) return;
    const newOtp = Array(6).fill('');
    for (let i = 0; i < pasted.length; i++) {
      newOtp[i] = pasted[i];
    }
    setOtp(newOtp);
    const nextIndex = Math.min(pasted.length, 5);
    inputRefs.current[nextIndex]?.focus();

    if (pasted.length === 6) {
      handleSubmit(pasted);
    }
  }

  async function handleResend() {
    if (!email || resendCooldown > 0) return;
    try {
      await resendOtp(email);
      setResendCooldown(30);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resend code');
    }
  }

  if (!email) return null;

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh]">
      <h1 className="text-2xl font-bold mb-2">Verify your email</h1>
      <p className="text-gray-400 text-sm mb-6">
        Enter the 6-digit code sent to <span className="text-white">{email}</span>
      </p>

      <div
        className={`flex gap-3 mb-6 ${shake ? 'animate-shake' : ''}`}
        onPaste={handlePaste}
      >
        {otp.map((digit, index) => (
          <input
            key={index}
            ref={(el) => { inputRefs.current[index] = el; }}
            type="text"
            inputMode="numeric"
            maxLength={1}
            value={digit}
            onChange={(e) => handleChange(index, e.target.value)}
            onKeyDown={(e) => handleKeyDown(index, e)}
            disabled={loading}
            className="w-12 h-14 text-center text-2xl font-bold bg-gray-900 border border-gray-700 rounded-lg text-white focus:outline-none focus:border-purple-500 disabled:opacity-50"
            autoFocus={index === 0}
          />
        ))}
      </div>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      {loading && (
        <p className="text-gray-400 text-sm mb-4">Verifying...</p>
      )}

      <button
        onClick={handleResend}
        disabled={resendCooldown > 0}
        className="text-purple-400 hover:text-purple-300 text-sm disabled:text-gray-600 disabled:cursor-not-allowed transition-colors"
      >
        {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : 'Resend code'}
      </button>

      <p className="mt-6 text-gray-400 text-sm">
        Wrong email?{' '}
        <Link href="/register" className="text-purple-400 hover:text-purple-300">
          Go back
        </Link>
      </p>
    </div>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={
      <div className="flex flex-col items-center justify-center min-h-[60vh]">
        <p className="text-gray-400">Loading...</p>
      </div>
    }>
      <VerifyForm />
    </Suspense>
  );
}

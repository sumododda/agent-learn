import './globals.css';
import type { Metadata } from 'next';
import Link from 'next/link';
import {
  ClerkProvider,
  Show,
  SignInButton,
  UserButton,
} from '@clerk/nextjs';

export const metadata: Metadata = {
  title: 'agent-learn',
  description: 'AI-powered personalized course generation',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body className="bg-gray-950 text-gray-100 min-h-screen">
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
              <Show when="signed-out">
                <SignInButton mode="modal">
                  <button className="text-sm text-gray-400 hover:text-white transition-colors">
                    Sign In
                  </button>
                </SignInButton>
              </Show>
              <Show when="signed-in">
                <UserButton afterSignOutUrl="/" />
              </Show>
            </div>
          </header>
          <main className="max-w-4xl mx-auto px-6 py-8">
            {children}
          </main>
        </body>
      </html>
    </ClerkProvider>
  );
}

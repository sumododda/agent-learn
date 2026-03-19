import './globals.css';
import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = {
  title: 'agent-learn',
  description: 'AI-powered personalized course generation',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
          <Link href="/" className="text-lg font-semibold text-white hover:text-gray-300">
            agent-learn
          </Link>
          <Link href="/library" className="text-sm text-gray-400 hover:text-white transition-colors">
            My Courses
          </Link>
        </header>
        <main className="max-w-4xl mx-auto px-6 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}

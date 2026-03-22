'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { Settings, Moon, Sun, LogOut } from 'lucide-react';
import { UserAvatar } from '@/components/UserAvatar';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export function Navbar() {
  const pathname = usePathname();
  const { isSignedIn, logout, userEmail } = useAuth();
  const { theme, setTheme } = useTheme();

  return (
    <nav className="sticky top-0 z-50 h-12 border-b border-border bg-background">
      <div className="flex h-full items-center justify-between px-4 max-w-7xl mx-auto">
        {/* Left: Logo */}
        <Link href="/" className="text-sm font-semibold text-foreground">
          agent-learn
        </Link>

        {/* Center: Nav links */}
        {isSignedIn && (
          <div className="flex items-center gap-6">
            <Link
              href="/"
              className={`text-sm ${pathname === '/' ? 'text-primary' : 'text-muted-foreground hover:text-foreground'} transition-colors`}
            >
              Create
            </Link>
            <Link
              href="/library"
              className={`text-sm ${pathname === '/library' ? 'text-primary' : 'text-muted-foreground hover:text-foreground'} transition-colors`}
            >
              Library
            </Link>
          </div>
        )}

        {/* Right: Actions */}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          >
            <Sun className="h-4 w-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
            <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
          </Button>

          {isSignedIn && (
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button variant="ghost" size="icon" className="h-8 w-8" />}
              >
                <UserAvatar email={userEmail} />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {userEmail && (
                  <div className="px-2 py-1.5 text-xs text-muted-foreground truncate max-w-[200px]">
                    {userEmail}
                  </div>
                )}
                <DropdownMenuItem
                  render={<Link href="/settings" />}
                >
                  <Settings className="mr-2 h-4 w-4" />
                  Settings
                </DropdownMenuItem>
                <DropdownMenuItem onClick={logout}>
                  <LogOut className="mr-2 h-4 w-4" />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {!isSignedIn && (
            <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/login" />}>
              Sign in
            </Button>
          )}
        </div>
      </div>
    </nav>
  );
}

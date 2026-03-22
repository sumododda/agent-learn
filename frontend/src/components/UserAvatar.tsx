'use client';

const COLORS = [
  'bg-red-500',
  'bg-orange-500',
  'bg-amber-500',
  'bg-emerald-500',
  'bg-teal-500',
  'bg-cyan-500',
  'bg-blue-500',
  'bg-indigo-500',
  'bg-violet-500',
  'bg-purple-500',
  'bg-fuchsia-500',
  'bg-pink-500',
];

function hashCode(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

interface UserAvatarProps {
  email: string | null;
  size?: 'sm' | 'md';
}

export function UserAvatar({ email, size = 'sm' }: UserAvatarProps) {
  const initial = email ? email[0].toUpperCase() : '?';
  const color = email ? COLORS[hashCode(email) % COLORS.length] : 'bg-muted';
  const dims = size === 'sm' ? 'h-6 w-6 text-xs' : 'h-10 w-10 text-sm';

  return (
    <div
      className={`${dims} ${color} rounded-full flex items-center justify-center text-white font-medium select-none`}
    >
      {initial}
    </div>
  );
}

import { NextRequest, NextResponse } from 'next/server';

// CI noop: frontend source touchpoint for deploy pipeline verification.
// Auth is handled client-side via AuthContext + localStorage JWT.
// The backend validates tokens on every API call.
// This middleware is a pass-through.
export function proxy(_req: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    '/(api|trpc)(.*)',
  ],
};

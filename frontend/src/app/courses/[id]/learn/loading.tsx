export default function Loading() {
  return (
    <div className="h-screen flex">
      <div className="w-60 border-r border-border p-4 space-y-3">
        <div className="h-3 w-16 bg-muted animate-pulse rounded" />
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-8 bg-muted animate-pulse rounded" />
        ))}
      </div>
      <div className="flex-1 p-12 max-w-[680px] mx-auto space-y-4">
        <div className="h-4 w-24 bg-muted animate-pulse rounded" />
        <div className="h-8 w-64 bg-muted animate-pulse rounded" />
        <div className="h-4 w-full bg-muted animate-pulse rounded mt-8" />
        <div className="h-4 w-full bg-muted animate-pulse rounded" />
        <div className="h-4 w-3/4 bg-muted animate-pulse rounded" />
      </div>
      <div className="w-[300px] border-l border-border p-4 space-y-3">
        <div className="h-8 bg-muted animate-pulse rounded" />
        <div className="h-20 bg-muted animate-pulse rounded" />
        <div className="h-20 bg-muted animate-pulse rounded" />
      </div>
    </div>
  );
}

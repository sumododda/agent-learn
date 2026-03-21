export default function Loading() {
  return (
    <div className="space-y-4 p-8 max-w-3xl mx-auto">
      <div className="h-8 w-48 bg-muted animate-pulse rounded" />
      <div className="h-4 w-full bg-muted animate-pulse rounded" />
      <div className="h-4 w-3/4 bg-muted animate-pulse rounded" />
      <div className="h-32 w-full bg-muted animate-pulse rounded-lg" />
    </div>
  );
}

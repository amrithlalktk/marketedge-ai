import Link from "next/link";

export default function NotFound() {
  return (
    <div className="py-16 text-center">
      <h1 className="text-lg font-semibold">Page not found</h1>
      <p className="mt-1 text-sm text-muted">The page you requested does not exist.</p>
      <Link href="/" className="link mt-4 inline-block">
        Back to dashboard
      </Link>
    </div>
  );
}

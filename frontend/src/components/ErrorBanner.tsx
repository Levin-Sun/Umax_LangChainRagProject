import { errText } from "@/lib/api";

export function ErrorBanner({ error }: { error?: unknown }) {
  if (!error) return null;
  return <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{errText(error)}</div>;
}

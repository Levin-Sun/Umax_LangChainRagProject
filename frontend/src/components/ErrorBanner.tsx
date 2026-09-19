import { errText } from "@/lib/api";

export function ErrorBanner({ error }: { error?: unknown }) {
  if (!error) return null;
  return <div role="alert" className="rounded bg-red-50 p-2 text-sm text-red-700">{errText(error)}</div>;
}

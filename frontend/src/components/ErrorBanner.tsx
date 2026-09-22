import { errText } from "@/lib/api";

export function ErrorBanner({ error }: { error?: unknown }) {
  if (!error) return null;
  return <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{errText(error)}</div>;
}

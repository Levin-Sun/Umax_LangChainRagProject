"use client";
// 自助改密弹窗（spec §3.1 change-password）：旧口令 + 新口令两遍（前端断言一致才发请求）。
// 成功后的会话副作用（logout() + 跳 /admin/login）经 onChanged 注入而非内部 useAuth——
// brief 的组件测试以 {api, onClose} 裸渲染（无 AuthProvider），useAuth 会直接 throw；
// 会话清理是调用方（Nav）职责，行为不变、依赖方向更干净。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorBanner } from "@/components/ErrorBanner";
import { callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";

export default function ChangePasswordDialog({ api, onClose, onChanged }:
  { api: Client; onClose: () => void; onChanged?: () => void | Promise<void> }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [formErr, setFormErr] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (newPw !== confirm) { setFormErr("两次输入的新口令不一致"); return; }
    setFormErr(null);
    setErr(null);
    setBusy(true);
    try {
      await callVoid(api.POST(P.authChangePassword, { body: { old_password: oldPw, new_password: newPw } }));
      await onChanged?.();
      onClose();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
         onClick={() => { if (!busy) onClose(); }}>
      <form role="dialog" aria-label="修改口令" onSubmit={(e) => { e.preventDefault(); submit(); }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
        <h3 className="text-h2 font-semibold text-ink-1">修改口令</h3>
        {([["旧口令", oldPw, setOldPw], ["新口令", newPw, setNewPw], ["确认新口令", confirm, setConfirm]] as const).map(
          ([zh, val, set]) => (
            <label key={zh} className="block space-y-1">
              <span className="text-h3 font-medium text-ink-2">{zh}</span>
              <Input type="password" value={val} className="h-9 rounded-lg border-border"
                     onChange={(e) => set(e.target.value)} />
            </label>
          ))}
        <p className="text-caption text-ink-3">新口令至少 8 位；修改成功后其他设备的会话会被吊销并退回登录页。</p>
        {formErr && <p className="text-body text-destructive">{formErr}</p>}
        <ErrorBanner error={err} />
        <div className="flex gap-2">
          <Button type="submit" disabled={busy} className="h-9 rounded-lg">{busy ? "提交中…" : "确认修改"}</Button>
          <Button type="button" variant="ghost" className="h-9 rounded-lg text-ink-3" disabled={busy}
                  onClick={() => onClose()}>
            取消
          </Button>
        </div>
      </form>
    </div>
  );
}

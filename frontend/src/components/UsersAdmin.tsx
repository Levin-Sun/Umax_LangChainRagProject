"use client";
// 用户管理（/admin/users）：列表 + 新建（email/姓名/初始口令/角色）+ 行内操作
// （角色下拉 PATCH、启停 PATCH、重置口令内联双输入、member 授权弹窗 PUT grants）。
// 自身保护：当前登录 admin 的行不渲染"禁用/启用"、角色下拉禁用（与后端 400 双保险，spec §6）。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { AuthMe, KbOut, UserOut } from "@/lib/types";

export const USER_ROLES = ["admin", "member"] as const;
const STATUS_LABEL: Record<string, string> = { active: "正常", disabled: "已禁用" };
const emptyForm = { email: "", name: "", password: "", role: "member" };

export default function UsersAdmin({ api, me }: { api: Client; me: AuthMe }) {
  const [form, setForm] = useState({ ...emptyForm });
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 行内操作（角色/启停/重置/授权）共用 busy 防双击竞态——与 ModelsAdmin 同型（任务7欠账②）
  const [rowBusy, setRowBusy] = useState(false);
  const [rowErr, setRowErr] = useState<unknown>(null);
  const [grantFor, setGrantFor] = useState<UserOut | null>(null);
  const [grantKbs, setGrantKbs] = useState<number[]>([]);
  const [resetFor, setResetFor] = useState<UserOut | null>(null);
  const [quotaFor, setQuotaFor] = useState<UserOut | null>(null);
  const [quotaForm, setQuotaForm] = useState<{ daily: string; monthly: string }>({ daily: "", monthly: "" });
  const [resetPw, setResetPw] = useState("");
  const [resetPw2, setResetPw2] = useState("");
  const list = useAsync(() => call(api.GET(P.users)) as Promise<UserOut[]>);
  // 库列表供授权弹窗 checkbox（GET /kb；后端已按角色过滤，admin 全量）
  const kbs = useAsync(() => call(api.GET(P.kb)) as Promise<KbOut[]>);
  const rows = list.data ?? [];
  const kbRows = kbs.data ?? [];

  async function create() {
    for (const k of ["email", "name", "password"] as const) {
      if (!form[k].trim()) { setFormErr("邮箱/姓名/初始口令均为必填"); return; }
    }
    // 体验反馈①②：与后端同口径的本地校验——把 422 的"请求不合法"挡在请求发出前
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim())) {
      setFormErr("邮箱格式不正确，应为 name@example.com"); return;
    }
    if (form.password.length < 8) { setFormErr("初始口令至少 8 位"); return; }
    setFormErr(null);
    setBusy(true);
    try {
      await call(api.POST(P.users, { body: form }));
      setForm({ ...emptyForm });
      list.reload();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function patch(u: UserOut, body: { role?: string; status?: string; password?: string }) {
    if (rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      await call(api.PATCH(P.user, { params: { path: { user_id: u.id } }, body }));
      setResetFor(null); setResetPw(""); setResetPw2("");
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  async function del(u: UserOut) {
    if (rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      await callVoid(api.DELETE(P.user, { params: { path: { user_id: u.id } } }));
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  function openQuota(u: UserOut) {
    setRowErr(null);
    setQuotaFor(u);
    setQuotaForm({ daily: u.daily_token_limit?.toString() ?? "",
                   monthly: u.monthly_token_limit?.toString() ?? "" });
  }

  // 重置口令走弹窗（与配额同型）：此前是行内展开两个输入框，一行被撑高、整张表跟着跳，
  // 触发按钮还跟着换行——破坏列表的等距节奏（真机反馈 2026-10-09）。
  function openReset(u: UserOut) {
    setRowErr(null);
    setResetFor(u);
    setResetPw("");
    setResetPw2("");
  }

  async function saveReset() {
    if (!resetFor || rowBusy || resetPw.length < 8 || resetPw !== resetPw2) return;
    await patch(resetFor, { password: resetPw });   // 成功后由 patch 统一关窗 + 刷新列表
  }

  async function saveQuota() {
    if (!quotaFor || rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      // 空串 = 清空为不限（显式 null 才会被后端当真，缺席=不动）
      await call(api.PATCH(P.user, {
        params: { path: { user_id: quotaFor.id } },
        body: { daily_token_limit: quotaForm.daily.trim() ? Number(quotaForm.daily) : null,
                monthly_token_limit: quotaForm.monthly.trim() ? Number(quotaForm.monthly) : null },
      }));
      setQuotaFor(null);
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  function openGrant(u: UserOut) {
    setRowErr(null);
    setGrantFor(u);
    setGrantKbs(u.kb_ids ?? []);
  }

  async function saveGrant() {
    if (!grantFor || rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      // 整集合替换（PUT 语义）：按库列表顺序装配，避免集合乱码序进 body
      const ordered = kbRows.filter((k) => grantKbs.includes(k.id)).map((k) => k.id);
      await call(api.PUT(P.userGrants, {
        params: { path: { user_id: grantFor.id } }, body: { kb_ids: ordered },
      }));
      setGrantFor(null);
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-6">
      <AdminBanner error={list.error ?? kbs.error ?? rowErr} />
      <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
        <table className="w-full text-body text-ink-2">
          <thead><tr className="border-b border-border bg-muted/60 text-left text-h3 font-medium text-ink-2">
            <th className="px-4 py-2.5 font-medium">邮箱</th><th className="py-2.5 font-medium">姓名</th>
            <th className="py-2.5 font-medium">角色</th><th className="py-2.5 font-medium">状态</th>
            <th className="py-2.5 font-medium">授权库</th><th className="py-2.5 font-medium">配额（日/月 token）</th>
            <th className="py-2.5 pr-4 font-medium">操作</th></tr></thead>
          <tbody>
            {rows.map((u) => {
              const self = me.email === u.email;
              return (
                <tr key={u.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-2.5">{u.email}</td>
                  <td>{u.name}</td>
                  <td>
                    <select aria-label={`角色 ${u.email}`} value={u.role} disabled={self || rowBusy}
                            className="rounded-lg border border-border px-2 py-1 text-body text-ink-1 outline-none focus:border-ring"
                            onChange={(e) => patch(u, { role: e.target.value })}>
                      {USER_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td><Badge variant={u.status === "disabled" ? "destructive" : "secondary"} className="rounded-full font-normal">
                    {STATUS_LABEL[u.status] ?? u.status}</Badge></td>
                  <td className="font-medium">{u.role === "admin" ? "全部" : (u.kb_ids ?? []).join(", ") || "—"}</td>
                  <td>{u.daily_token_limit === null && u.monthly_token_limit === null
                    ? "不限" : `${u.daily_token_limit ?? "不限"} / ${u.monthly_token_limit ?? "不限"}`}</td>
                  <td className="space-x-1.5 pr-4">
                    {u.role === "member" && (
                      <Button size="sm" variant="outline" className="h-7 rounded-lg" disabled={rowBusy}
                              onClick={() => openGrant(u)}>授权</Button>
                    )}
                    {!self && (u.status === "active"
                      ? <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive hover:text-destructive"
                                disabled={rowBusy} onClick={() => patch(u, { status: "disabled" })}>禁用</Button>
                      : <Button size="sm" variant="ghost" className="h-7 rounded-lg" disabled={rowBusy}
                                onClick={() => patch(u, { status: "active" })}>启用</Button>)}
                    <Button size="sm" variant="outline" className="h-7 rounded-lg" disabled={rowBusy}
                            onClick={() => openQuota(u)}>配额</Button>
                    <Button size="sm" variant="ghost" className="h-7 rounded-lg text-ink-1" disabled={rowBusy}
                            onClick={() => openReset(u)}>
                      重置口令
                    </Button>
                    {!self && (
                      <Button size="sm" variant="ghost" className="h-7 rounded-lg text-destructive hover:text-destructive"
                              disabled={rowBusy} onClick={() => void del(u)}>删除</Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <form className="max-w-md space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm"
            onSubmit={(e) => { e.preventDefault(); create(); }}>
        <h3 className="text-h2 font-semibold">新建用户</h3>
        {([["email", "邮箱"], ["name", "姓名"], ["password", "初始口令"]] as const).map(([k, zh]) => (
          <label key={k} className="block space-y-1">
            <span className="text-h3 font-medium text-ink-2">{zh}</span>
            <Input placeholder={k} type={k === "password" ? "password" : "text"} className="h-9 rounded-lg border-border"
                   value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">角色</span>
          <select className="w-full rounded-lg border border-border px-3 py-2 text-body text-ink-1 outline-none focus:border-ring"
                  value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {USER_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        {formErr && <p className="text-body text-destructive">{formErr}</p>}
        <Button type="submit" disabled={busy} className="h-9 rounded-lg">{busy ? "提交中…" : "新建用户"}</Button>
      </form>
      {quotaFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
             onClick={() => { if (!rowBusy) setQuotaFor(null); }}>
          <form role="dialog" aria-label={`配额 ${quotaFor.email}`}
                onClick={(e) => e.stopPropagation()}
                onSubmit={(e) => { e.preventDefault(); void saveQuota(); }}
                className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
            <h3 className="text-h2 font-semibold text-ink-1">用量配额 · {quotaFor.name}</h3>
            <p className="text-caption text-ink-3">
              留空即不限。用量按 token 计（问答与识图；窗口为 UTC 自然日/自然月），达到上限后该用户提问会被拒绝。
            </p>
            {([["每日上限（tokens）", "daily", quotaForm.daily], ["每月上限（tokens）", "monthly", quotaForm.monthly]] as const).map(
              ([label, key, val]) => (
                <label key={key} className="block space-y-1">
                  <span className="text-h3 font-medium text-ink-2">{label}</span>
                  <Input aria-label={label} inputMode="numeric" placeholder="不限" disabled={rowBusy}
                         className="h-9 rounded-lg border-border" value={val}
                         onChange={(e) => setQuotaForm((p) => ({ ...p, [key]: e.target.value }))} />
                </label>
              ))}
            <div className="flex gap-2">
              <Button type="submit" size="sm" className="h-8 rounded-lg" disabled={rowBusy}>保存</Button>
              <Button type="button" size="sm" variant="ghost" className="h-8 rounded-lg text-ink-3"
                      disabled={rowBusy} onClick={() => setQuotaFor(null)}>取消</Button>
            </div>
          </form>
        </div>
      )}
      {resetFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
             onClick={() => { if (!rowBusy) setResetFor(null); }}>
          <form role="dialog" aria-label={`重置口令 ${resetFor.email}`}
                onClick={(e) => e.stopPropagation()}
                onSubmit={(e) => { e.preventDefault(); void saveReset(); }}
                className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
            <h3 className="text-h2 font-semibold text-ink-1">重置口令 · {resetFor.name}</h3>
            <p className="text-caption text-ink-3">
              设一个新口令并告知本人；对方下次登录会被要求再自行改一次（与新建用户同口径）。
            </p>
            <label className="block space-y-1">
              <span className="text-h3 font-medium text-ink-2">新口令</span>
              <Input aria-label="新口令" type="password" placeholder="至少 8 位" disabled={rowBusy}
                     className="h-9 rounded-lg border-border" value={resetPw}
                     onChange={(e) => setResetPw(e.target.value)} />
            </label>
            <label className="block space-y-1">
              <span className="text-h3 font-medium text-ink-2">确认新口令</span>
              <Input aria-label="确认新口令" type="password" placeholder="再输一遍" disabled={rowBusy}
                     className="h-9 rounded-lg border-border" value={resetPw2}
                     onChange={(e) => setResetPw2(e.target.value)} />
            </label>
            {resetPw.length > 0 && resetPw.length < 8 && (
              <p className="text-caption text-destructive">新口令至少 8 位</p>
            )}
            {resetPw2.length > 0 && resetPw !== resetPw2 && (
              <p className="text-caption text-destructive">两次输入不一致</p>
            )}
            <div className="flex gap-2">
              <Button type="submit" size="sm" className="h-8 rounded-lg"
                      disabled={rowBusy || resetPw.length < 8 || resetPw !== resetPw2}>保存</Button>
              <Button type="button" size="sm" variant="ghost" className="h-8 rounded-lg text-ink-3"
                      disabled={rowBusy} onClick={() => setResetFor(null)}>取消</Button>
            </div>
          </form>
        </div>
      )}
      {grantFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
             onClick={() => { if (!rowBusy) setGrantFor(null); }}>
          <div role="dialog" aria-label={`授权 ${grantFor.email}`} onClick={(e) => e.stopPropagation()}
               className="w-full max-w-sm space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
            <h3 className="text-h2 font-semibold text-ink-1">授权知识库 · {grantFor.name}</h3>
            <p className="text-caption text-ink-3">整集合替换保存（PUT grants）；admin 隐式全库无需授权。</p>
            <div className="space-y-1.5">
              {kbRows.map((k) => (
                <label key={k.id} className="flex items-center gap-2 text-body text-ink-2">
                  <input type="checkbox" checked={grantKbs.includes(k.id)} disabled={rowBusy}
                         onChange={(e) => setGrantKbs((prev) =>
                           e.target.checked ? [...prev, k.id] : prev.filter((id) => id !== k.id))} />
                  {k.name}
                </label>
              ))}
              {!kbs.loading && kbRows.length === 0 && <p className="text-caption text-ink-3">暂无知识库</p>}
            </div>
            <div className="flex gap-2">
              {/* 收编⑯：库列表 loading/出错时禁用保存——否则 kbRows 为空会静默把整集合替换为空（抹掉授权） */}
              <Button size="sm" className="h-8 rounded-lg"
                      disabled={rowBusy || kbs.loading || !!kbs.error} onClick={() => void saveGrant()}>保存</Button>
              <Button size="sm" variant="ghost" className="h-8 rounded-lg text-ink-3" disabled={rowBusy}
                      onClick={() => setGrantFor(null)}>取消</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

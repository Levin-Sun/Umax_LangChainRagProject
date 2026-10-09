"use client";
// 模型后台。三条路径，按客户认知成本排序：
//   1) 一键配齐（主）：选厂商 → 粘一把 key → 系统按内置标本配好该厂商的能力，并逐个试调
//      ——客户不必知道"embedding 走哪个端点""模型名叫什么"（真机为此连踩四个坑）。
//   2) 能力矩阵：把"现在能做什么 / 没配会怎样"摆在明面上，替代让客户理解四类场景的负担。
//   3) 手动登记（折叠）：自建模型、内网网关、厂商代理这些长尾的出口，字段语义不变。
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Combobox } from "@/components/ui/combobox";
import { Select } from "@/components/ui/select";
import AdminBanner from "@/components/AdminBanner";
import { call, callVoid, type Client } from "@/lib/api";
import { P } from "@/lib/paths";
import { useAsync } from "@/lib/hooks";
import type { BundleOut, CatalogVendor, ModelCatalog, ModelOut } from "@/lib/types";

// 与 backend/app/main.py 的 SCENARIOS 集合保持同步（契约经 pattern 入约，改集合走四步契约工作流）
export const SCENARIOS = ["chat", "embedding", "rerank", "vision"] as const;

const emptyForm = { scenario: "chat", provider: "", base_url: "", api_key: "", model_name: "", capabilities: {} as Record<string, unknown>, fallback_rank: 0, enabled: true, is_default: false };

export default function ModelsAdmin({ api }: { api: Client }) {
  const [form, setForm] = useState({ ...emptyForm });
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 任务7欠账②：行内操作（启停/删除）独立 busy 防双击竞态，错误经 ErrorBanner 呈现
  const [rowBusy, setRowBusy] = useState(false);
  const [rowErr, setRowErr] = useState<unknown>(null);
  const list = useAsync(() => call(api.GET(P.models)) as Promise<ModelOut[]>);
  // 厂商标本 + 一键配齐
  const catalog = useAsync(() => call(api.GET(P.modelCatalog)) as Promise<ModelCatalog>);
  const [vendorId, setVendorId] = useState("");
  const [bundleKey, setBundleKey] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [bundleBusy, setBundleBusy] = useState(false);
  const [bundleOut, setBundleOut] = useState<BundleOut | null>(null);
  const [bundleErr, setBundleErr] = useState<unknown>(null);
  const vendors = catalog.data?.vendors ?? [];
  const vendor = vendors.find((v) => v.id === vendorId) ?? null;
  const CAP_ORDER = ["chat", "embedding", "vision", "rerank"] as const;

  function pickVendor(id: string) {
    setVendorId(id);
    setBundleOut(null);
    const v = vendors.find((x) => x.id === id);
    setPicked(v ? v.capabilities.map((c) => c.key) : []);   // 默认全勾：一劳永逸由产品保证
  }

  function missingEssentials(v: CatalogVendor): string[] {
    const have = new Set(v.capabilities.map((c) => c.key));
    return ["chat", "embedding"].filter((k) => !have.has(k));
  }

  async function runBundle() {
    if (!vendor || bundleBusy) return;
    if (!bundleKey.trim()) {
      setBundleErr(new Error("先粘贴该厂商的 API 密钥"));
      return;
    }
    setBundleBusy(true);
    setBundleErr(null);
    try {
      const out = await call(api.POST(P.modelsBundle, {
        body: { vendor_id: vendor.id, api_key: bundleKey, capabilities: picked, test: true },
      })) as unknown as BundleOut;
      setBundleOut(out);
      list.reload();      // 列表与"现在能做什么"立刻反映新配置
    } catch (e) {
      setBundleErr(e);
    } finally {
      setBundleBusy(false);
    }
  }

  function capStatus(key: string): { configured: boolean; model: string } {
    const hit = (list.data ?? []).find((m) => m.scenario === key && m.enabled);
    return hit ? { configured: true, model: hit.model_name } : { configured: false, model: "" };
  }

  /** 接入档案名（按量计费 / 原生端点…）：用（厂商 + 地址）从标本里反查。
   *  同一个厂商会有多行，光看厂商名分不清是哪条档案。 */
  function profileLabel(m: ModelOut): string {
    const v = vendors.find((x) => x.name === m.provider);
    return v?.profiles.find((p) => p.base_url === m.base_url)?.name ?? "";
  }

  // ---- 编辑已有配置（改 key / 换模型 / 调优先级）----
  // 没有它，改一个 key 只能"删了重登"——那一瞬间该能力就没有模型可用了。
  // 密钥留空＝不修改：库里只有密文、界面只有打码，所以不能拿打码值回写。
  const [editFor, setEditFor] = useState<ModelOut | null>(null);
  const [editForm, setEditForm] = useState({ scenario: "chat", provider: "", base_url: "",
                                            model_name: "", api_key: "", fallback_rank: 0, enabled: true });

  function openEdit(m: ModelOut) {
    setRowErr(null);
    setEditFor(m);
    setEditForm({ scenario: m.scenario, provider: m.provider, base_url: m.base_url,
                  model_name: m.model_name, api_key: "", fallback_rank: m.fallback_rank,
                  enabled: m.enabled });
  }

  async function saveEdit() {
    if (!editFor || rowBusy) return;
    if (!editForm.provider.trim() || !editForm.base_url.trim() || !editForm.model_name.trim()) {
      setRowErr(new Error("厂商、接口地址、模型名均不能为空"));
      return;
    }
    setRowBusy(true);
    setRowErr(null);
    try {
      const body: Record<string, unknown> = {
        scenario: editForm.scenario, provider: editForm.provider.trim(),
        base_url: editForm.base_url.trim(), model_name: editForm.model_name.trim(),
        fallback_rank: editForm.fallback_rank, enabled: editForm.enabled,
      };
      if (editForm.api_key.trim()) body.api_key = editForm.api_key.trim();
      await call(api.PATCH(P.model, { params: { path: { model_id: editFor.id } }, body }));
      setEditFor(null);
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  async function register() {
    for (const k of ["provider", "base_url", "api_key", "model_name"] as const) {
      if (!String(form[k]).trim()) {
        setFormErr("厂商/地址/key/模型名均为必填");
        return;
      }
    }
    setFormErr(null);
    setBusy(true);
    try {
      await call(api.POST(P.models, { body: form }));
      setForm({ ...emptyForm });
      list.reload();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(m: ModelOut) {
    if (rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      await call(api.PATCH(P.model, { params: { path: { model_id: m.id } }, body: { enabled: !m.enabled } }));
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  async function remove(m: ModelOut) {
    if (rowBusy) return;
    setRowBusy(true);
    setRowErr(null);
    try {
      await callVoid(api.DELETE(P.model, { params: { path: { model_id: m.id } } }));
      list.reload();
    } catch (e) {
      setRowErr(e);
    } finally {
      setRowBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-6 py-6">
      <AdminBanner error={list.error ?? rowErr ?? catalog.error ?? bundleErr} />

      <section className="space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-sm">
        <div>
          <h3 className="text-h2 font-semibold">模型接入</h3>
          <p className="mt-1 text-caption text-ink-3">
            两步：选厂商、粘贴一把 key。系统按这家厂商能提供的能力自动配好（接口地址与模型名由内置标本给出），
            并逐个试调一次，把厂商的真实返回直接显示出来。
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {/* 用 div 而不是 label 包：手动登记表单里也有"厂商/API 密钥"字段，
              两处都用 label 会让可访问名重名（测试与读屏都会指不清）。控件自身的 aria-label 已足够。 */}
          <div className="block space-y-1">
            <span className="text-h3 font-medium text-ink-2">厂商</span>
            <Combobox label="厂商（可搜索）" value={vendorId} onChange={pickVendor}
                      placeholder="点击或输入搜索：de / 百炼 / abl / jyx"
                      options={vendors.map((v) => ({ value: v.id, label: v.name,
                        keywords: [...v.aliases, ...v.pinyin],
                        hint: v.verified ? "已实测" : "未实测" }))} />
          </div>
          <div className="block space-y-1">
            <span className="text-h3 font-medium text-ink-2">API 密钥</span>
            <Input aria-label="厂商 API 密钥" type="password" className="h-9 rounded-lg border-border"
                   placeholder="在厂商控制台获取" value={bundleKey}
                   onChange={(e) => setBundleKey(e.target.value)} />
          </div>
        </div>
        {vendor?.note && <p className="text-caption text-ink-3">{vendor.name} · {vendor.note}</p>}
        {vendor && (
          <div className="space-y-2">
            <p className="text-h3 font-medium text-ink-2">将自动配置</p>
            <ul className="divide-y divide-border rounded-lg border border-border">
              {vendor.capabilities.map((c) => (
                <li key={c.key} className="flex items-center gap-2 px-3 py-2">
                  <input type="checkbox"
                         aria-label={`启用${catalog.data?.capability_labels?.[c.key] ?? c.key}`}
                         checked={picked.includes(c.key)}
                         onChange={(e) => setPicked((prev) => (e.target.checked
                           ? [...prev, c.key] : prev.filter((k) => k !== c.key)))} />
                  <span className="text-body text-ink-1">
                    {catalog.data?.capability_labels?.[c.key] ?? c.key} · {c.model}
                  </span>
                  <span className="ml-auto text-caption text-ink-3">
                    {vendor.profiles.find((x) => x.id === c.profile)?.name ?? ""}
                    {typeof c.dim === "number" ? ` · ${c.dim} 维` : ""}
                  </span>
                </li>
              ))}
              {!vendor.capabilities.length && (
                <li className="px-3 py-2 text-caption text-ink-3">
                  这家没有内置清单，请用下方「高级：手动登记」填地址与模型名
                </li>
              )}
            </ul>
            {missingEssentials(vendor).length > 0 && (
              <p className="text-caption text-destructive">
                这家不提供：{missingEssentials(vendor)
                  .map((k) => catalog.data?.capability_labels?.[k] ?? k).join("、")}
                ——需要再选一家厂商补上
              </p>
            )}
            <Button className="h-9 rounded-lg" disabled={bundleBusy || !picked.length}
                    onClick={() => void runBundle()}>
              {bundleBusy ? "配置中…" : "一键配齐并测试"}
            </Button>
          </div>
        )}
        {bundleOut && (
          <ul className="space-y-1 rounded-lg border border-border px-3 py-2">
            {bundleOut.items.map((i) => (
              <li key={i.capability} className="flex flex-wrap items-baseline gap-2 text-caption">
                <span className={i.ok === false ? "text-destructive" : "text-ink-1"}>
                  {i.ok === false ? "未通过" : i.ok === null ? "已登记" : "通过"} · {i.model}
                </span>
                <span className="text-ink-3">{i.action === "updated" ? "（更新了 key）" : "（新建）"}</span>
                {i.detail && <span className="text-ink-3">{i.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2 rounded-xl border border-border bg-card px-6 py-5 shadow-sm">
        <h3 className="text-h2 font-semibold">模型能力状态</h3>
        <p className="text-caption text-ink-3">状态取自当前配置；缺哪一项，右侧直接说清代价。</p>
        <div className="overflow-x-auto">
        <table aria-label="模型能力状态" className="w-full text-body text-ink-2">
          <thead><tr className="border-b border-border text-left text-caption font-normal text-ink-3">
            <th className="py-2 pr-3 font-normal">用途</th>
            <th className="py-2 pr-3 font-normal">状态</th>
            <th className="py-2 font-normal">没配会怎样</th></tr></thead>
          <tbody>
            {CAP_ORDER.map((key) => {
              const st = capStatus(key);
              return (
                <tr key={key} className="border-b border-border last:border-0">
                  <td className="py-2 pr-3 text-ink-1">{catalog.data?.capability_labels?.[key] ?? key}</td>
                  <td className="py-2 pr-3">
                    {st.configured
                      ? <span className="text-caption text-ink-1">已配 {st.model}</span>
                      : <span className="text-caption text-destructive">未配置</span>}
                  </td>
                  <td className="py-2 text-caption text-ink-3">{catalog.data?.capability_miss?.[key] ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </section>

      <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
        <table aria-label="模型配置列表" className="w-full text-body text-ink-2">
          <thead><tr className="border-b border-border bg-muted/60 text-left text-h3 font-medium text-ink-2">
            <th className="px-4 py-2.5 font-medium">场景</th><th className="py-2.5 font-medium">厂商</th>
            <th className="py-2.5 font-medium">模型</th>
            <th className="py-2.5 font-medium">API Key</th><th className="py-2.5 font-medium">fallback</th>
            <th className="py-2.5 pr-4 font-medium">操作</th></tr></thead>
          <tbody>
            {(list.data ?? []).map((m) => (
              <tr key={m.id} className="border-b border-border last:border-0">
                <td className="px-4 py-2.5"><Badge variant="secondary" className="rounded-full font-normal">{m.scenario}</Badge></td>
                {/* 厂商 + 接入档案名：同一个厂商常有多行（兼容模式 / 原生端点），
                    只显示厂商名仍分不清是哪条档案——而"精排必须走原生端点"正是靠这一列看出来的。 */}
                <td className="py-2.5" title={m.base_url}>
                  <div>{m.provider}</div>
                  {profileLabel(m) && <div className="text-caption text-ink-3">{profileLabel(m)}</div>}
                </td>
                <td className="py-2.5">{m.model_name}{m.is_default && <span className="ml-1 text-caption font-medium text-ink-1">默认</span>}
                  {!m.enabled && <span className="ml-1 text-caption text-ink-3">（停用）</span>}</td>
                <td className="py-2.5 font-mono text-caption">{m.api_key_masked}</td>
                <td className="py-2.5 font-medium">#{m.fallback_rank}</td>
                <td className="space-x-1.5 py-2.5 pr-4">
                  <Button size="sm" variant="outline" className="h-7 rounded-lg" disabled={rowBusy}
                          onClick={() => openEdit(m)}>编辑</Button>
                  <button role="switch" aria-checked={m.enabled} aria-label={`启用 ${m.model_name}`}
                          className={`rounded-full border px-2.5 py-0.5 text-body transition-colors ${
                            m.enabled ? "border-border text-ink-1 hover:bg-accent" : "border-border/60 text-ink-3"}`}
                          onClick={() => toggle(m)}>
                    {m.enabled ? "停用" : "启用"}
                  </button>
                  <Button size="sm" variant="ghost" className="h-7 px-2 text-destructive hover:text-destructive" onClick={() => remove(m)}>删除</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="rounded-xl border border-border bg-card px-6 py-4 shadow-sm">
        <summary className="cursor-pointer text-h3 font-medium text-ink-2">
          高级：手动登记（自建模型、内网网关、厂商代理）
        </summary>
      <form className="mt-4 max-w-md space-y-3"
            onSubmit={(e) => { e.preventDefault(); register(); }}>
        <h3 className="text-h2 font-semibold">手动登记模型</h3>
        <p className="text-caption text-ink-3">
          四个场景各自的用处：chat=问答生成；embedding=文档与问题的向量化；rerank=检索结果精排
          （登记后检索多做一道精排，不登记就直接用 RRF 融合结果，问答不受影响）；vision=传图提问与
          文档插图转文字。注意 rerank 走百炼「原生」端点——接口地址填
          https://dashscope.aliyuncs.com/api/v1
        </p>
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">场景</span>
          <Select value={form.scenario} onChange={(scenario) => setForm({ ...form, scenario })}
                  options={SCENARIOS.map((s) => ({ value: s, label: s }))}
                  className="w-full" />
        </label>
        {([["provider", "厂商"], ["base_url", "接口地址"], ["api_key", "API 密钥"], ["model_name", "模型名"]] as const).map(([k, zh]) => (
          <label key={k} className="block space-y-1">
            <span className="text-h3 font-medium text-ink-2">{zh}</span>
            <Input placeholder={k} value={String(form[k])} type={k === "api_key" ? "password" : "text"}
                   className="h-9 rounded-lg border-border"
                   onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <label className="block space-y-1">
          <span className="text-h3 font-medium text-ink-2">回退优先级</span>
          <Input type="number" placeholder="fallback_rank" value={form.fallback_rank} className="h-9 rounded-lg border-border"
                 onChange={(e) => setForm({ ...form, fallback_rank: Number(e.target.value) || 0 })} />
        </label>
        {formErr && <p className="text-body text-destructive">{formErr}</p>}
        <Button type="submit" disabled={busy} className="h-9 rounded-lg">{busy ? "提交中…" : "提交登记"}</Button>
      </form>
      </details>

      {editFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
             onClick={() => { if (!rowBusy) setEditFor(null); }}>
          <form role="dialog" aria-label={`编辑模型 ${editFor.model_name}`}
                onClick={(e) => e.stopPropagation()}
                onSubmit={(e) => { e.preventDefault(); void saveEdit(); }}
                className="w-full max-w-md space-y-3 rounded-xl border border-border bg-card px-6 py-5 shadow-lg">
            <h3 className="text-h2 font-semibold text-ink-1">编辑模型 · {editFor.model_name}</h3>
            <p className="text-caption text-ink-3">
              保存即生效（网关每次调用现读配置）。密钥留空表示不改，当前为 {editFor.api_key_masked}。
            </p>
            <label className="block space-y-1">
              <span className="text-h3 font-medium text-ink-2">场景</span>
              <Select value={editForm.scenario}
                      onChange={(scenario) => setEditForm((p) => ({ ...p, scenario }))}
                      options={SCENARIOS.map((s) => ({ value: s, label: s }))} className="w-full" />
            </label>
            {([["provider", "厂商"], ["base_url", "接口地址"], ["model_name", "模型名"]] as const).map(([k, zh]) => (
              <label key={k} className="block space-y-1">
                <span className="text-h3 font-medium text-ink-2">{zh}</span>
                <Input aria-label={zh} value={String(editForm[k])} disabled={rowBusy}
                       className="h-9 rounded-lg border-border"
                       onChange={(e) => setEditForm((p) => ({ ...p, [k]: e.target.value }))} />
              </label>
            ))}
            <label className="block space-y-1">
              <span className="text-h3 font-medium text-ink-2">密钥（留空不改）</span>
              <Input aria-label="密钥（留空不改）" type="password" disabled={rowBusy}
                     placeholder={editFor.api_key_masked} value={editForm.api_key}
                     className="h-9 rounded-lg border-border"
                     onChange={(e) => setEditForm((p) => ({ ...p, api_key: e.target.value }))} />
            </label>
            <label className="block space-y-1">
              <span className="text-h3 font-medium text-ink-2">回退优先级</span>
              <Input aria-label="回退优先级" type="number" disabled={rowBusy}
                     value={editForm.fallback_rank} className="h-9 rounded-lg border-border"
                     onChange={(e) => setEditForm((p) => ({ ...p, fallback_rank: Number(e.target.value) || 0 }))} />
            </label>
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" className="h-8 rounded-lg" disabled={rowBusy}>保存</Button>
              <Button type="button" size="sm" variant="ghost" className="h-8 rounded-lg text-ink-3"
                      disabled={rowBusy} onClick={() => setEditFor(null)}>取消</Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

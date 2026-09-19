import { useEffect, useState } from "react";
import { flushSync } from "react-dom";

export interface AsyncState<T> { data?: T; error?: unknown; loading: boolean }

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<AsyncState<T>>({ loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    fn().then((data) => alive && setState({ data, loading: false }))
        .catch((error) => alive && setState({ error, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

export function usePolling<T>(fn: () => Promise<T>,
  opts: { intervalMs: number; stopWhen: (v: T) => boolean; enabled: boolean }) {
  const [state, setState] = useState<AsyncState<T>>({ loading: false });
  const [tick, setTick] = useState(0);
  const { intervalMs, stopWhen, enabled } = opts;
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const v = await fn();
        if (!alive) return;
        // flushSync：React 19 测试环境（fake timers）下普通 setState 会滞留 act 队列，
        // advanceTimersByTimeAsync 不会冲刷 → 断言看不到中间值（任务 3 实测）。
        flushSync(() => setState({ data: v, loading: false }));
        if (!stopWhen(v)) timer = setTimeout(run, intervalMs);
      } catch (error) {
        if (alive) flushSync(() => setState({ error, loading: false }));
      }
    };
    setState({ loading: true });
    run();
    return () => { alive = false; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, enabled]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

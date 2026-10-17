import { useEffect, useRef } from "react";

/**
 * Sondagem robusta: a próxima chamada só é agendada quando a anterior termina (respostas lentas não se acumulam),
 * fica em pausa com o separador oculto (retoma logo que volta a estar visível) e espaça as tentativas depois de
 * erros (até 30 s). `fn` pode mudar entre renderizações: usa-se sempre a versão mais recente.
 */
export function usePolling(fn: () => Promise<unknown> | void, intervalMs: number, enabled = true) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    let stopped = false;
    let failures = 0;
    const schedule = () => {
      if (stopped) return;
      const delay = failures ? Math.min(30_000, intervalMs * 2 ** failures) : intervalMs;
      timer = window.setTimeout(tick, delay);
    };
    const tick = async () => {
      if (document.visibilityState !== "visible") return; // retoma no evento visibilitychange
      try {
        await latest.current();
        failures = 0;
      } catch {
        failures = Math.min(failures + 1, 5);
      }
      schedule();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible" && !stopped) {
        window.clearTimeout(timer);
        void tick();
      }
    };
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs, enabled]);
}

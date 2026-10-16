import { type RefObject, useEffect, useRef } from "react";

/** Dica mostrada junto aos leitores. */
export const VIDEO_KEYS_HINT = "Teclado: ← → 10 s · Espaço pausa";

// Campos onde as teclas pertencem ao campo (texto, listas, sliders). Caixas de seleção e botões não contam.
const TYPING = "input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]), textarea, select, "
  + "[contenteditable=''], [contenteditable='true'], [role=combobox], [role=slider], [role=textbox], [role=listbox]";

/**
 * Atalhos do leitor: ← / → recuam/avançam 10 s e Espaço põe em pausa/reproduz.
 * Não atua quando se escreve num campo, nem quando há uma janela (modal) aberta que não contém este leitor.
 */
export function useVideoKeys({ seek, toggle, anchor, enabled = true }: {
  seek: (delta: number) => void;
  toggle: () => void;
  anchor: RefObject<HTMLElement | null>;
  enabled?: boolean;
}) {
  const handlers = useRef({ seek, toggle });
  handlers.current = { seek, toggle };
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey || e.shiftKey || e.defaultPrevented) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== " ") return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest(TYPING)) return;
      const el = anchor.current;
      if (!el || !el.isConnected) return;
      // Com uma modal aberta, o foco está nela: só atua se o leitor estiver nessa modal.
      const dialog = target?.closest("[role=dialog]") ?? document.querySelector("[role=dialog]");
      if (dialog && !dialog.contains(el)) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.key === " ") handlers.current.toggle();
      else handlers.current.seek(e.key === "ArrowRight" ? 10 : -10);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [enabled, anchor]);
}

/** Atalhos para um elemento <video> HTML. */
export function useHtmlVideoKeys(video: RefObject<HTMLVideoElement | null>, enabled = true) {
  useVideoKeys({
    anchor: video,
    enabled,
    seek: (d) => {
      const v = video.current;
      if (!v) return;
      const end = Number.isFinite(v.duration) ? v.duration : Infinity;
      v.currentTime = Math.min(end, Math.max(0, v.currentTime + d));
    },
    toggle: () => {
      const v = video.current;
      if (!v) return;
      if (v.paused) void v.play().catch(() => undefined);
      else v.pause();
    },
  });
}

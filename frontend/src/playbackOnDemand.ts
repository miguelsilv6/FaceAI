import { useEffect, useRef, useState } from "react";

import { api } from "./api";
import { notifyError } from "./notify";
import type { Video } from "./types";
import { usePolling } from "./usePolling";

/** A cópia de visualização está a ser preparada (ou vai ser pedida logo a seguir). */
export const preparingCopy = (v: Video) =>
  v.playback_status === "on_demand" || v.playback_status === "pending" || v.playback_status === "running";

/**
 * Vídeos que o browser não abre ficam "on_demand": a cópia leve (só para visualização) é pedida quando o leitor
 * os mostra (no máximo `limit` pedidos automáticos; os restantes com `request`). Devolve os vídeos com o estado
 * atualizado (sondado enquanto a cópia é preparada). Um vídeo mais recente vindo do pai prevalece sobre o guardado.
 */
export function useOnDemandCopies(videos: Video[], { limit = Infinity, onUpdate }: {
  limit?: number; onUpdate?: () => void;
} = {}): { videos: Video[]; request: (v: Video, format?: "mp4" | "webm") => Promise<void>; set: (v: Video) => void } {
  // id → [versão atual, objeto do pai quando foi guardada]: se o pai mudar o objeto, o do pai prevalece
  const [fresh, setFresh] = useState<Map<number, [Video, Video]>>(() => new Map());
  const asked = useRef(new Set<number>());
  const update = useRef(onUpdate);
  update.current = onUpdate;
  const props = new Map(videos.map((v) => [v.id, v]));
  const merged = videos.map((v) => {
    const f = fresh.get(v.id);
    return f && f[1] === v ? f[0] : v;
  });
  const set = (u: Video) => {
    const base = props.get(u.id);
    if (base) setFresh((m) => new Map(m).set(u.id, [u, base]));
  };
  const request = async (v: Video, format?: "mp4" | "webm") => {
    asked.current.add(v.id);
    try {
      const u = await api.createPlaybackCopy(v.id, format);
      set(u);
      if (u.playback_status === "on_demand") asked.current.delete(v.id); // ainda não foi possível: volta a tentar
      update.current?.();
    } catch (e) {
      asked.current.delete(v.id); // tenta de novo quando o estado do vídeo mudar
      notifyError(e, "Não foi possível preparar o vídeo para o browser");
    }
  };
  const key = merged.map((v) => `${v.id}:${v.playback_status}`).join(",");
  const auto = useRef(0);
  useEffect(() => {
    for (const v of merged) {
      if (v.playback_status !== "on_demand" || asked.current.has(v.id) || auto.current >= limit) continue;
      auto.current += 1;
      void request(v);
    }
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const busy = merged.some((v) => v.playback_status === "pending" || v.playback_status === "running");
  const caseId = merged[0]?.case_id;
  usePolling(async () => {
    if (caseId == null) return;
    const vs = await api.videos(caseId);
    let done = false;
    for (const v of vs) {
      const cur = merged.find((x) => x.id === v.id);
      if (!cur) continue;
      if (preparingCopy(cur) && !preparingCopy(v)) done = true;
      set(v);
    }
    if (done) update.current?.(); // ficou pronta (ou falhou): o pai atualiza a lista
  }, 3000, busy && caseId != null);
  return { videos: merged, request, set };
}

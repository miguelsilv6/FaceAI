import { useEffect, useRef, useState } from "react";

import { api } from "./api";
import { notifyError } from "./notify";
import type { Video } from "./types";
import { usePolling } from "./usePolling";

export const preparingCopy = (v: Video) =>
  v.playback_status === "on_demand" || v.playback_status === "pending" || v.playback_status === "running";

/**
 * Vídeos que o browser não abre ficam "on_demand": a cópia leve (só para visualização) é pedida quando o leitor
 * os mostra. Devolve os vídeos com o estado atualizado (sondado enquanto a cópia é preparada).
 */
export function useOnDemandCopies(videos: Video[]): Video[] {
  const [fresh, setFresh] = useState<Map<number, Video>>(() => new Map());
  const asked = useRef(new Set<number>());
  const merged = videos.map((v) => fresh.get(v.id) ?? v);
  const key = merged.map((v) => `${v.id}:${v.playback_status}`).join(",");
  useEffect(() => {
    for (const v of merged) {
      if (v.playback_status !== "on_demand" || asked.current.has(v.id)) continue;
      asked.current.add(v.id);
      api.createPlaybackCopy(v.id)
        .then((u) => setFresh((m) => new Map(m).set(u.id, u)))
        .catch(notifyError);
    }
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const busy = merged.some((v) => v.playback_status === "pending" || v.playback_status === "running");
  const caseId = merged[0]?.case_id;
  usePolling(async () => {
    if (caseId == null) return;
    const ids = new Set(merged.map((v) => v.id));
    const vs = await api.videos(caseId);
    setFresh((m) => {
      const next = new Map(m);
      for (const v of vs) if (ids.has(v.id)) next.set(v.id, v);
      return next;
    });
  }, 3000, busy && caseId != null);
  return merged;
}

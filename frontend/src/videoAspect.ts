import type { CSSProperties } from "react";

import type { Video } from "./types";

/** Tamanho de apresentação (píxeis não quadrados corrigidos) ou o gravado. */
export function displaySize(v: Video): [number, number] {
  const d = v.aspect?.display;
  return d && d[0] > 0 && d[1] > 0 ? [d[0], d[1]] : [v.width || 16, v.height || 9];
}

/** A proporção foi corrigida (o browser esticaria a imagem de outra forma). */
export function aspectCorrected(v: Video): boolean {
  const [w, h] = displaySize(v);
  return !!v.width && !!v.height && (w !== v.width || h !== v.height);
}

/** Estilo de um <video> com a proporção de apresentação; `maxHeight` em CSS (ex.: "70vh", "420px"). */
export function videoStyle(v: Video, maxHeight: string, base: CSSProperties = {}): CSSProperties {
  if (!aspectCorrected(v)) return { width: "100%", maxHeight, ...base };
  const [w, h] = displaySize(v);
  return { ...base, display: "block", margin: "0 auto", width: `min(100%, calc(${maxHeight} * ${w / h}))`,
    height: "auto", aspectRatio: `${w} / ${h}`, objectFit: "fill" };
}

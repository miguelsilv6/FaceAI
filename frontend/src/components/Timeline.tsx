import { Box, Tooltip } from "@mantine/core";

import { formatTime } from "../api";
import type { Detection, Segment } from "../types";

interface Props {
  realTime?: (seconds: number) => string;
  duration: number;
  segments: Segment[];
  detections: Detection[];
  currentTime: number;
  onSeek: (t: number) => void;
}

/** Barra temporal do vídeo: segmentos de aparição (blocos) e deteções individuais (marcas). */
export default function Timeline({ duration, segments, detections, currentTime, onSeek, realTime }: Props) {
  const pct = (t: number) => `${Math.min(100, Math.max(0, (t / Math.max(duration, 0.001)) * 100))}%`;
  return (
    <Box
      pos="relative"
      h={36}
      style={{ background: "var(--mantine-color-default-hover)", borderRadius: 6, cursor: "pointer", overflow: "hidden" }}
      onClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        onSeek(((e.clientX - rect.left) / rect.width) * duration);
      }}
      role="slider"
      aria-label="Linha temporal"
      aria-valuemin={0}
      aria-valuemax={duration}
      aria-valuenow={currentTime}
    >
      {segments.map((s, i) => (
        <Tooltip key={i} label={`${realTime?.(s.start_s) ? `${realTime(s.start_s)} → ${realTime(s.end_s).slice(11)} · ` : ""}`
          + `${formatTime(s.start_s)} → ${formatTime(s.end_s)} · ${s.count} deteções · sim. ${s.best_similarity.toFixed(3)}`}>
          <Box
            pos="absolute"
            top={4}
            bottom={4}
            left={pct(s.start_s)}
            style={{
              width: `max(6px, calc(${pct(s.end_s - s.start_s)}))`,
              background: "var(--mantine-color-red-6)",
              opacity: 0.75,
              borderRadius: 4,
            }}
            onClick={(e) => {
              e.stopPropagation();
              onSeek(s.start_s);
            }}
          />
        </Tooltip>
      ))}
      {detections.map((d) => (
        <Box key={d.id} pos="absolute" top={0} h={6} w={2} left={pct(d.timestamp_s)}
          style={{ background: d.is_match ? "var(--mantine-color-red-9)" : "var(--mantine-color-orange-5)" }} />
      ))}
      <Box pos="absolute" top={0} bottom={0} w={2} left={pct(currentTime)} style={{ background: "var(--mantine-color-blue-6)" }} />
    </Box>
  );
}

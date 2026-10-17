import { ActionIcon, Badge, Card, Group, Image, Stack, Text, Tooltip } from "@mantine/core";
import { IconCheck, IconPhotoSearch, IconX } from "@tabler/icons-react";

import { api, formatTime } from "../api";
import type { ReviewStatus, Track, Video } from "../types";

interface Props {
  track: Track;
  video?: Video;
  cameraLabel?: string; // "Local · Câmara"
  realStart?: string;
  onSeek: (track: Track) => void;
  onReview: (track: Track, status: ReviewStatus) => void;
  onOpenFrame: (detectionId: number) => void;
}

/** Cores nomeadas (clothing.py) → amostra visual. */
const SWATCH: Record<string, string> = {
  vermelho: "#d32f2f", laranja: "#f57c00", amarelo: "#fbc02d", verde: "#388e3c", ciano: "#0097a7",
  azul: "#1976d2", roxo: "#7b1fa2", rosa: "#d81b60", preto: "#111", "cinzento-escuro": "#555",
  cinzento: "#999", branco: "#f5f5f5",
};
const swatch = (name?: string) => {
  if (!name) return "#ccc";
  const base = name.replace(/-escuro$/, "");
  return name.endsWith("-escuro") && SWATCH[base] ? `color-mix(in srgb, ${SWATCH[base]} 55%, black)` : SWATCH[name] ?? "#ccc";
};

const REVIEW: Record<ReviewStatus, [string, string]> = {
  pending: ["Por rever", "gray"], confirmed: ["Confirmado", "green"], rejected: ["Rejeitado", "red"],
};

/** Track de corpo: amostra (corpo de uma face com correspondência) ou aparição só pelo vestuário. */
export default function BodyTrackCard({ track, video, cameraLabel, realStart, onSeek, onReview, onOpenFrame }: Props) {
  const sample = track.evidence === "face+vestuário";
  const [label, color] = REVIEW[track.review_status];
  const colors = track.colors ?? {};
  return (
    <Card withBorder padding="xs" style={{ opacity: track.review_status === "rejected" ? 0.55 : 1 }}>
      <Card.Section style={{ cursor: "pointer", background: "var(--mantine-color-default-hover)" }} onClick={() => onSeek(track)}>
        {track.best_detection_id != null && (
          <Image loading="lazy" decoding="async" src={api.detectionImageUrl(track.best_detection_id, "crop")} h={170} fit="contain" alt={`Corpo ${track.id}`} />
        )}
      </Card.Section>
      <Stack gap={4} mt={6}>
        <Group justify="space-between" wrap="nowrap">
          <Badge color={sample ? "orange" : "grape"} variant="filled" size="sm">
            {sample ? "Amostra (face)" : "Só vestuário"}
          </Badge>
          {!sample && (
            <Tooltip label={`Distância ReID ${track.reid_distance?.toFixed(3)} · cor ${track.color_distance?.toFixed(3)}`}>
              <Text size="xs" ff="monospace">d {track.reid_distance?.toFixed(3)}</Text>
            </Tooltip>
          )}
        </Group>
        <Group gap={6} wrap="nowrap">
          {["tronco", "pernas"].map((r) => (
            <Tooltip key={r} label={`${r}: ${colors[r] ?? "?"}`}>
              <Group gap={4} wrap="nowrap">
                <span style={{ width: 14, height: 14, borderRadius: 3, background: swatch(colors[r]),
                  border: "1px solid var(--mantine-color-default-border)", display: "inline-block" }} />
                <Text size="xs">{colors[r] ?? "?"}</Text>
              </Group>
            </Tooltip>
          ))}
        </Group>
        <Text size="xs" c="dimmed" truncate title={video?.filename}>{video ? cameraLabel || `#${video.id} ${video.filename}` : ""}</Text>
        <Text size="xs" ff="monospace">
          {realStart ? `${realStart} · ` : ""}{formatTime(track.start_s)} → {formatTime(track.end_s)}
        </Text>
        {track.note && <Text size="xs" c={track.note.includes("ambíguo") ? "orange" : "dimmed"}>{track.note}</Text>}
        <Group justify="space-between" wrap="nowrap">
          <Badge color={color} variant="light" size="sm">{label}</Badge>
          <Group gap={4} wrap="nowrap">
            {track.best_detection_id != null && (
              <Tooltip label="Ver frame">
                <ActionIcon variant="subtle" size="sm" aria-label="Ver frame do corpo"
                  onClick={() => onOpenFrame(track.best_detection_id!)}><IconPhotoSearch size={16} /></ActionIcon>
              </Tooltip>
            )}
            <Tooltip label="Confirmar (é a mesma pessoa)">
              <ActionIcon variant={track.review_status === "confirmed" ? "filled" : "light"} color="green" size="sm"
                aria-label="Confirmar vestuário"
                onClick={() => onReview(track, track.review_status === "confirmed" ? "pending" : "confirmed")}>
                <IconCheck size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Rejeitar (outra pessoa)">
              <ActionIcon variant={track.review_status === "rejected" ? "filled" : "light"} color="red" size="sm"
                aria-label="Rejeitar vestuário"
                onClick={() => onReview(track, track.review_status === "rejected" ? "pending" : "rejected")}>
                <IconX size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </Stack>
    </Card>
  );
}

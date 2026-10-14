import { ActionIcon, Badge, Button, Card, Group, Image, Menu, Stack, Text, Tooltip } from "@mantine/core";
import { IconCheck, IconPhotoSearch, IconUserShare, IconUsersGroup, IconX } from "@tabler/icons-react";

import { api, formatTime } from "../api";
import { personColor } from "../personColors";
import type { ReviewStatus, Track } from "../types";
import QualityBadge from "./QualityBadge";

interface Props {
  track: Track;
  onSeek: (t: number) => void;
  onReview: (track: Track, status: ReviewStatus) => void;
  onShowDetections: (track: Track) => void;
  realStart?: string;
  realEnd?: string;
  onOpenFrame?: (detectionId: number) => void;
  /** Modo descoberta: pessoas para onde o track pode ser movido. */
  personIds?: number[];
  onMove?: (track: Track, personId: number | null) => void;
  /** Várias pessoas procuradas (modo procurar): nome da pessoa do track. */
  personLabel?: string;
  onFindSimilar?: (track: Track) => void;
}

const REVIEW: Record<ReviewStatus, [string, string]> = {
  pending: ["Por rever", "gray"],
  confirmed: ["Confirmado", "green"],
  rejected: ["Rejeitado", "red"],
};

/** Um track = a mesma face em vários frames. A revisão aplica-se a todas as deteções do track. */
export default function TrackCard({ track, onSeek, onReview, onShowDetections, realStart, realEnd, onOpenFrame,
  personIds, onMove, personLabel, onFindSimilar }: Props) {
  const [label, color] = REVIEW[track.review_status];
  const sim = 1 - track.track_distance;
  return (
    <Card withBorder padding="xs" style={{ opacity: track.review_status === "rejected" ? 0.55 : 1 }}>
      <Card.Section style={{ cursor: "pointer" }} onClick={() => onSeek(track.start_s)}>
        <Group gap={0} wrap="nowrap" grow>
          {track.best_detection_id != null && (
            <Image src={api.detectionImageUrl(track.best_detection_id, "crop")} h={120} fit="cover" alt={`Track ${track.id}`} />
          )}
          {track.reference_id != null && (
            <Image src={api.referenceImageUrl(track.reference_id, true)} h={120} fit="cover" alt="Referência" />
          )}
        </Group>
      </Card.Section>
      <Stack gap={4} mt={6}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" fw={600}>Track #{track.id}</Text>
          {personLabel ? (
            <Group gap={4} wrap="nowrap">
              <Badge color={personColor(track.person_id)} variant="light" size="sm" maw={110}>{personLabel}</Badge>
              <Tooltip label="Similaridade do track (média das 3 melhores distâncias)">
                <Badge color={track.is_match ? "red" : "orange"} variant="filled" size="sm">{(sim * 100).toFixed(1)}%</Badge>
              </Tooltip>
            </Group>
          ) : track.person_id != null ? (
            <Tooltip label={`Distância ao centro do grupo da pessoa: ${track.track_distance.toFixed(3)}`}>
              <Badge color="blue" variant="filled" size="sm">Pessoa #{track.person_id}</Badge>
            </Tooltip>
          ) : (
            <Tooltip label="Similaridade do track (média das 3 melhores distâncias)">
              <Badge color={track.is_match ? "red" : "orange"} variant="filled" size="sm">{(sim * 100).toFixed(1)}%</Badge>
            </Tooltip>
          )}
        </Group>
        {realStart && (
          <Text size="xs" ff="monospace" fw={600}>{realStart} → {realEnd?.slice(11)}</Text>
        )}
        <Text size="xs" ff="monospace" c={realStart ? "dimmed" : undefined}>
          {realStart ? "vídeo " : ""}{formatTime(track.start_s)} → {formatTime(track.end_s)}
        </Text>
        <Group gap={6} wrap="nowrap">
          <QualityBadge quality={track.best_quality} />
          <Text size="xs" c="dimmed">{track.n_faces} faces · {track.n_saved} guardadas</Text>
        </Group>
        <Group justify="space-between" wrap="nowrap">
          <Tooltip label={track.reviewed_by ? `${track.reviewed_by}${track.review_note ? `: ${track.review_note}` : ""}` : "Sem revisão"}>
            <Badge color={color} variant="light" size="sm">{label}</Badge>
          </Tooltip>
          <Group gap={4} wrap="nowrap">
            {onOpenFrame && track.best_detection_id != null && (
              <Tooltip label="Ver frame da melhor face">
                <ActionIcon variant="subtle" size="sm" aria-label="Ver frame"
                  onClick={() => onOpenFrame(track.best_detection_id!)}>
                  <IconPhotoSearch size={16} />
                </ActionIcon>
              </Tooltip>
            )}
            {onFindSimilar && (
              <Tooltip label="Procurar esta pessoa noutras análises">
                <ActionIcon variant="subtle" size="sm" aria-label="Procurar noutras análises"
                  onClick={() => onFindSimilar(track)}>
                  <IconUsersGroup size={16} />
                </ActionIcon>
              </Tooltip>
            )}
            {onMove && personIds && (
              <Menu position="bottom-end" withinPortal>
                <Menu.Target>
                  <Tooltip label="Mover para outra pessoa (corrigir o agrupamento)">
                    <ActionIcon variant="subtle" size="sm" aria-label="Mover track"><IconUserShare size={16} /></ActionIcon>
                  </Tooltip>
                </Menu.Target>
                <Menu.Dropdown mah={300} style={{ overflowY: "auto" }}>
                  <Menu.Item onClick={() => onMove(track, null)}>Nova pessoa</Menu.Item>
                  <Menu.Divider />
                  {personIds.filter((id) => id !== track.person_id).map((id) => (
                    <Menu.Item key={id} onClick={() => onMove(track, id)}>Pessoa #{id}</Menu.Item>
                  ))}
                </Menu.Dropdown>
              </Menu>
            )}
            <Tooltip label="Confirmar o track inteiro">
              <ActionIcon variant={track.review_status === "confirmed" ? "filled" : "light"} color="green" size="sm"
                aria-label="Confirmar track"
                onClick={() => onReview(track, track.review_status === "confirmed" ? "pending" : "confirmed")}>
                <IconCheck size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Rejeitar o track inteiro">
              <ActionIcon variant={track.review_status === "rejected" ? "filled" : "light"} color="red" size="sm"
                aria-label="Rejeitar track"
                onClick={() => onReview(track, track.review_status === "rejected" ? "pending" : "rejected")}>
                <IconX size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        <Button size="compact-xs" variant="subtle" onClick={() => onShowDetections(track)}>Ver deteções</Button>
      </Stack>
    </Card>
  );
}

import { ActionIcon, Badge, Card, Group, Image, Stack, Text, Tooltip } from "@mantine/core";
import { IconCheck, IconPhotoSearch, IconX } from "@tabler/icons-react";

import { api, formatTime } from "../api";
import QualityBadge from "./QualityBadge";
import type { Detection, ReviewStatus } from "../types";

interface Props {
  det: Detection;
  onSeek: (det: Detection) => void;
  onReview: (det: Detection, status: ReviewStatus) => void;
  onOpenFrame: (det: Detection) => void;
}

const REVIEW: Record<ReviewStatus, [string, string]> = {
  pending: ["Por rever", "gray"],
  confirmed: ["Confirmada", "green"],
  rejected: ["Rejeitada", "red"],
};

export default function DetectionCard({ det, onSeek, onReview, onOpenFrame }: Props) {
  const [label, color] = REVIEW[det.review_status];
  return (
    <Card withBorder padding="xs" style={{ opacity: det.review_status === "rejected" ? 0.55 : 1 }}>
      <Card.Section style={{ cursor: "pointer" }} onClick={() => onSeek(det)}>
        <Group gap={0} wrap="nowrap" grow>
          <Image src={api.detectionImageUrl(det.id, "crop")} h={120} fit="cover" alt={`Deteção ${det.id}`} />
          <Image src={api.referenceImageUrl(det.reference_id, true)} h={120} fit="cover" alt="Referência" />
        </Group>
      </Card.Section>
      <Stack gap={4} mt={6}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" fw={600} ff="monospace">{formatTime(det.timestamp_s)}</Text>
          <Badge color={det.is_match ? "red" : "orange"} variant="filled" size="sm">
            {(det.similarity * 100).toFixed(1)}%
          </Badge>
        </Group>
        <Group gap={6} wrap="nowrap">
          <QualityBadge quality={det.quality} sharpness={det.sharpness} sizeScore={det.size_score} frontal={det.frontal} />
          <Text size="xs" c="dimmed" truncate>
            fr. {det.frame_index} · d {det.distance.toFixed(3)}{det.track_id != null ? ` · T${det.track_id}` : ""}
            {det.is_match ? "" : " · cand."}
          </Text>
        </Group>
        <Group justify="space-between" wrap="nowrap">
          <Tooltip label={det.reviewed_by ? `${det.reviewed_by}${det.review_note ? `: ${det.review_note}` : ""}` : "Sem revisão"}>
            <Badge color={color} variant="light" size="sm">{label}</Badge>
          </Tooltip>
          <Group gap={4} wrap="nowrap">
            <Tooltip label="Ver frame completo">
              <ActionIcon variant="subtle" size="sm" onClick={() => onOpenFrame(det)} aria-label="Ver frame">
                <IconPhotoSearch size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Confirmar">
              <ActionIcon variant={det.review_status === "confirmed" ? "filled" : "light"} color="green" size="sm"
                onClick={() => onReview(det, det.review_status === "confirmed" ? "pending" : "confirmed")} aria-label="Confirmar">
                <IconCheck size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Rejeitar (falso positivo)">
              <ActionIcon variant={det.review_status === "rejected" ? "filled" : "light"} color="red" size="sm"
                onClick={() => onReview(det, det.review_status === "rejected" ? "pending" : "rejected")} aria-label="Rejeitar">
                <IconX size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </Stack>
    </Card>
  );
}

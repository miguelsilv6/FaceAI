import { ActionIcon, Badge, Button, Card, Group, Image, Menu, Stack, Text, Tooltip } from "@mantine/core";
import { IconArrowsJoin, IconCheck, IconUserPlus, IconUsersGroup, IconX } from "@tabler/icons-react";

import { api, formatTime } from "../api";
import type { Person, ReviewStatus } from "../types";
import QualityBadge from "./QualityBadge";

interface Props {
  person: Person;
  selected: boolean;
  others: Person[];
  firstRealTime?: string;
  onSelect: (p: Person) => void;
  onReview: (p: Person, status: ReviewStatus) => void;
  onMerge: (p: Person, into: Person) => void;
  onUseAsReference: (p: Person) => void;
  onFindSimilar?: (p: Person) => void;
}

const REVIEW: Record<Person["review_status"], [string, string]> = {
  pending: ["Por rever", "gray"],
  confirmed: ["Confirmada", "green"],
  rejected: ["Rejeitada", "red"],
  mixed: ["Revisão parcial", "yellow"],
};

/** Pessoa distinta encontrada no modo descoberta (grupo de tracks com faces semelhantes). */
export default function PersonCard({ person, selected, others, firstRealTime, onSelect, onReview, onMerge,
  onUseAsReference, onFindSimilar }: Props) {
  const [label, color] = REVIEW[person.review_status];
  const status = person.review_status;
  return (
    <Card withBorder padding="xs" style={{ opacity: status === "rejected" ? 0.55 : 1,
      outline: selected ? "2px solid var(--mantine-color-blue-6)" : undefined }}>
      <Card.Section style={{ cursor: "pointer" }} onClick={() => onSelect(person)}>
        {person.best_detection_id != null && (
          <Image loading="lazy" decoding="async" src={api.detectionImageUrl(person.best_detection_id, "crop")} h={140} fit="cover"
            alt={`Pessoa ${person.person_id}`} />
        )}
      </Card.Section>
      <Stack gap={4} mt={6}>
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" fw={600}>Pessoa #{person.person_id}</Text>
          <QualityBadge quality={person.best_quality} />
        </Group>
        <Text size="xs" c="dimmed">
          {person.n_tracks} track(s) · {person.n_faces} faces · {person.video_ids.length} vídeo(s)
        </Text>
        <Text size="xs" ff="monospace" title="Primeira aparição">
          {firstRealTime || `vídeo #${person.first.video_id} · ${formatTime(person.first.start_s)}`}
        </Text>
        <Group justify="space-between" wrap="nowrap">
          <Badge color={color} variant="light" size="sm">{label}</Badge>
          <Group gap={4} wrap="nowrap">
            <Tooltip label="Confirmar a pessoa (todos os tracks)">
              <ActionIcon variant={status === "confirmed" ? "filled" : "light"} color="green" size="sm"
                aria-label="Confirmar pessoa"
                onClick={() => onReview(person, status === "confirmed" ? "pending" : "confirmed")}>
                <IconCheck size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Rejeitar (não é uma face / irrelevante)">
              <ActionIcon variant={status === "rejected" ? "filled" : "light"} color="red" size="sm"
                aria-label="Rejeitar pessoa"
                onClick={() => onReview(person, status === "rejected" ? "pending" : "rejected")}>
                <IconX size={16} />
              </ActionIcon>
            </Tooltip>
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <Tooltip label="Juntar com outra pessoa (é a mesma)">
                  <ActionIcon variant="light" size="sm" aria-label="Juntar pessoas" disabled={!others.length}>
                    <IconArrowsJoin size={16} />
                  </ActionIcon>
                </Tooltip>
              </Menu.Target>
              <Menu.Dropdown mah={300} style={{ overflowY: "auto" }}>
                <Menu.Label>Juntar a pessoa #{person.person_id} com…</Menu.Label>
                {others.map((o) => (
                  <Menu.Item key={o.person_id} onClick={() => onMerge(person, o)}
                    leftSection={o.best_detection_id != null
                      ? <Image loading="lazy" decoding="async" src={api.detectionImageUrl(o.best_detection_id, "crop")} w={28} h={28} radius="sm" />
                      : undefined}>
                    Pessoa #{o.person_id} ({o.n_faces} faces)
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
        <Stack gap={4}>
          <Button size="xs" fullWidth variant={selected ? "filled" : "light"} onClick={() => onSelect(person)}>
            {selected ? "Selecionada" : "Ver aparições"}
          </Button>
          <Tooltip label="Usar a melhor face como amostra para procurar esta pessoa (modo com amostra)">
            <Button size="xs" fullWidth variant="default" leftSection={<IconUserPlus size={14} />}
              disabled={person.best_detection_id == null} onClick={() => onUseAsReference(person)}>
              Usar como amostra
            </Button>
          </Tooltip>
          {onFindSimilar && (
            <Tooltip label="Procurar esta pessoa noutras análises">
              <Button size="xs" fullWidth variant="default" leftSection={<IconUsersGroup size={14} />}
                onClick={() => onFindSimilar(person)}>
                Procurar noutras análises
              </Button>
            </Tooltip>
          )}
        </Stack>
      </Stack>
    </Card>
  );
}

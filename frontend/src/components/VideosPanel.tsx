import { Badge, Button, Card, Group, Loader, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconChevronRight, IconClock, IconMapPin, IconMapPinPlus, IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraName } from "../places";
import type { Location, Video } from "../types";

interface Props {
  caseId: number;
  videos: Video[];
  locations: Location[];
  onChange: () => void;
}

/** Locais do caso (estabelecimentos ou zonas de interesse). Os vídeos importam-se na página de cada local. */
export default function VideosPanel({ caseId, videos, locations, onChange }: Props) {
  const navigate = useNavigate();
  const [newName, setNewName] = useState<string | null>(null);
  const open = (id: number) => navigate(`/cases/${caseId}/locations/${id}`);

  const busy = videos.some((v) => v.playback_status === "pending" || v.playback_status === "running"
    || v.clock_auto_status === "pending" || v.clock_auto_status === "running");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(onChange, 3000);
    return () => clearInterval(t);
  }, [busy, onChange]);

  const createLocation = async () => {
    if (!newName?.trim()) return;
    try {
      const loc = await api.createLocation(caseId, newName.trim());
      notifyOk(`Local "${loc.name}" criado. Importe os vídeos das câmaras desse local.`);
      setNewName(null);
      onChange();
      open(loc.id);
    } catch (e) {
      notifyError(e, "Não foi possível criar o local");
    }
  };

  const convertAll = async () => {
    try {
      const vs = await api.createCaseCopies(caseId);
      const queued = vs.filter((v) => v.playback_status === "pending" || v.playback_status === "running").length;
      notifyOk(queued ? `${queued} vídeo(s) na fila de conversão para o browser.` : "Todos os vídeos já têm cópia de reprodução.");
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <Stack>
      <Group justify="space-between">
        {newName === null ? (
          <Button leftSection={<IconMapPinPlus size={16} />} onClick={() => setNewName("")}>Novo local</Button>
        ) : (
          <Group gap="xs">
            <TextInput placeholder="Nome do local (ex.: Café Central, Estação de Campanhã)" w={360} value={newName}
              data-autofocus aria-label="Nome do novo local" onChange={(e) => setNewName(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter") createLocation(); if (e.key === "Escape") setNewName(null); }} />
            <Button onClick={createLocation} disabled={!newName.trim()}>Criar local</Button>
            <Button variant="default" onClick={() => setNewName(null)}>Cancelar</Button>
          </Group>
        )}
        {videos.length > 0 && (
          <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={convertAll}>
            Criar cópias de reprodução de todos os vídeos
          </Button>
        )}
      </Group>
      <Text size="sm" c="dimmed">
        Cada local é um estabelecimento ou zona de interesse para o caso, com as suas câmaras. Abra um local para
        importar os vídeos, dar nome às câmaras e acertar a hora.
      </Text>
      {locations.length === 0 ? (
        <Card withBorder>
          <Text c="dimmed">Ainda não há locais. Crie um local (ex.: "Café Central") e importe nele os vídeos das
            várias câmaras desse local.</Text>
        </Card>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
          {locations.map((l) => {
            const vs = videos.filter((v) => v.location_id === l.id);
            const synced = vs.filter((v) => v.dvr_start).length;
            const working = vs.filter((v) => v.playback_status === "pending" || v.playback_status === "running"
              || v.clock_auto_status === "pending" || v.clock_auto_status === "running").length;
            return (
              <Card key={l.id} withBorder data-testid={`location-${l.id}`} style={{ cursor: "pointer" }}
                onClick={() => open(l.id)}>
                <Stack gap={6}>
                  <Group justify="space-between" wrap="nowrap">
                    <Group gap="xs" wrap="nowrap" miw={0}>
                      <IconMapPin size={20} color="var(--mantine-color-indigo-6)" />
                      <Title order={5} lineClamp={1}>{l.name}</Title>
                    </Group>
                    <IconChevronRight size={18} color="var(--mantine-color-dimmed)" />
                  </Group>
                  <Group gap="xs">
                    <Badge variant="light">{vs.length} câmara{vs.length === 1 ? "" : "s"}</Badge>
                    {vs.length > 0 && <Text size="xs" c="dimmed">{formatTime(l.duration_s)} de vídeo</Text>}
                  </Group>
                  {vs.length > 0 ? (
                    <>
                      <Text size="xs" c="dimmed" lineClamp={2}>{vs.map(cameraName).join(" · ")}</Text>
                      <Group gap="xs">
                        <Badge size="xs" variant="light" color={synced === vs.length ? "teal" : "orange"}
                          leftSection={<IconClock size={10} />}>
                          hora real {synced}/{vs.length}
                        </Badge>
                        {working > 0 && (
                          <Group gap={4}><Loader size={10} /><Text size="xs" c="dimmed">a processar {working}</Text></Group>
                        )}
                      </Group>
                    </>
                  ) : (
                    <Text size="xs" c="dimmed">Sem vídeos — abra o local para importar.</Text>
                  )}
                  <Button size="xs" variant="light" mt={4} rightSection={<IconChevronRight size={14} />}
                    onClick={(e) => { e.stopPropagation(); open(l.id); }}>
                    Abrir local
                  </Button>
                </Stack>
              </Card>
            );
          })}
        </SimpleGrid>
      )}
    </Stack>
  );
}

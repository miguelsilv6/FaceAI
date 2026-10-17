import { Alert, Badge, Button, Card, Group, Loader, SimpleGrid, Stack, Text, TextInput, Title, Tooltip } from "@mantine/core";
import {
  IconChevronRight, IconClock, IconDatabase, IconFolders, IconMapPin, IconMapPinPlus, IconRefresh, IconShieldCheck,
  IconShieldX,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatBytes, formatTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraName } from "../places";
import BulkImportModal from "./BulkImportModal";
import type { EvidenceCheck, Location, Video } from "../types";
import { usePolling } from "../usePolling";

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
  const [bulk, setBulk] = useState(false);
  const open = (id: number) => navigate(`/cases/${caseId}/locations/${id}`);

  const busy = videos.some((v) => v.playback_status === "pending" || v.playback_status === "running"
    || v.clock_auto_status === "pending" || v.clock_auto_status === "running");
  usePolling(onChange, 3000, busy);

  // Verificação de integridade (SHA-256 recalculado em segundo plano no servidor)
  const [check, setCheck] = useState<EvidenceCheck | null>(null);
  useEffect(() => {
    api.evidenceStatus(caseId).then((c) => setCheck(c.running || c.finished_at ? c : null)).catch(() => undefined);
  }, [caseId]);
  useEffect(() => {
    if (!check?.running) return;
    const t = setTimeout(() => {
      api.evidenceStatus(caseId).then((c) => {
        setCheck(c);
        if (!c.running) onChange();
      }).catch(() => undefined);
    }, 2000);
    return () => clearTimeout(t);
  }, [check, caseId, onChange]);
  const verify = async () => {
    try {
      setCheck(await api.verifyEvidence(caseId));
    } catch (e) {
      notifyError(e);
    }
  };
  const altered = videos.filter((v) => v.verified_ok === false);

  const createLocation = async () => {
    if (!newName?.trim()) return;
    try {
      const loc = await api.createLocation(caseId, newName.trim());
      notifyOk(`Local "${loc.name}" criado. Importe os vídeos desse local.`);
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
      <BulkImportModal caseId={caseId} locations={locations} opened={bulk} onClose={() => setBulk(false)}
        onChange={onChange} />
      <Group justify="space-between">
        {newName === null ? (
          <Group gap="xs">
            <Button leftSection={<IconMapPinPlus size={16} />} onClick={() => setNewName("")}>Novo local</Button>
            <Button variant="light" leftSection={<IconFolders size={16} />} onClick={() => setBulk(true)}>
              Importar pastas</Button>
          </Group>
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
          <Group gap="xs">
            <Tooltip label="Recalcula o SHA-256 de cada vídeo original e compara-o com o da importação (deteta alterações ou ficheiros em falta). Fica na auditoria.">
              <Button size="xs" variant="light" color="teal" leftSection={<IconShieldCheck size={14} />}
                loading={!!check?.running} onClick={verify}>Verificar integridade</Button>
            </Tooltip>
            <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={convertAll}>
              Criar cópias de reprodução de todos os vídeos
            </Button>
          </Group>
        )}
      </Group>
      {check?.running && (
        <Text size="sm" c="dimmed">A verificar a integridade: {check.done ?? 0} de {check.total || "?"} vídeo(s)
          {check.current ? ` · ${check.current}` : ""}…</Text>
      )}
      {check && !check.running && check.finished_at && (
        <Alert color={check.failed?.length ? "red" : "teal"} withCloseButton onClose={() => setCheck(null)}
          icon={check.failed?.length ? <IconShieldX size={18} /> : <IconShieldCheck size={18} />}
          title={check.failed?.length ? `${check.failed.length} vídeo(s) não correspondem ao original importado`
            : `Integridade verificada: ${check.ok} vídeo(s) idênticos aos importados`}>
          {check.failed?.map((f) => <Text key={f.video_id} size="sm">#{f.video_id} {f.filename}: {f.reason}</Text>)}
        </Alert>
      )}
      {check?.error && <Alert color="red" title="A verificação falhou">{check.error}</Alert>}
      {altered.length > 0 && !check?.finished_at && (
        <Alert color="red" icon={<IconShieldX size={18} />}
          title={`${altered.length} vídeo(s) falharam a última verificação de integridade`}>
          {altered.map((v) => <Text key={v.id} size="sm">#{v.id} {cameraName(v)}</Text>)}
        </Alert>
      )}
      <Text size="sm" c="dimmed">
        Cada local é um estabelecimento ou zona de interesse para o processo, com os seus vídeos. Abra um local para
        importar os vídeos, dar-lhes nome e acertar a hora.
      </Text>
      {locations.length === 0 ? (
        <Card withBorder>
          <Text c="dimmed">Ainda não há locais. Crie um local (ex.: "Café Central") e importe nele os vídeos
            desse local.</Text>
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
                    <Badge variant="light">{vs.length} vídeo{vs.length === 1 ? "" : "s"}</Badge>
                    {vs.length > 0 && <Text size="xs" c="dimmed">{formatTime(l.duration_s)} de vídeo</Text>}
                    {vs.length > 0 && (
                      <Tooltip label={`Originais ${formatBytes(l.originals_bytes)} · cópias de reprodução ${formatBytes(l.playback_bytes)}`}>
                        <Badge size="sm" variant="default" leftSection={<IconDatabase size={10} />}>{formatBytes(l.size_bytes)}</Badge>
                      </Tooltip>
                    )}
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

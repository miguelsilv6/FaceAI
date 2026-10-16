import {
  ActionIcon, Badge, Button, Card, Group, Loader, Menu, Stack, Table, Text, TextInput, Tooltip,
} from "@mantine/core";
import {
  IconArrowsExchange, IconCheck, IconClock, IconDots, IconMapPin, IconMapPinPlus, IconMovie, IconPencil,
  IconPlayerPlay, IconRefresh, IconTrash, IconX,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { api, formatBytes, formatTime, realTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import { cameraName, groupByLocation } from "../places";
import type { Job, Location, Video } from "../types";
import ClockModal from "./ClockModal";
import DeleteVideoModal from "./DeleteVideoModal";
import PlaybackBadge from "./PlaybackBadge";
import UploadZone from "./UploadZone";
import VideoPlayerModal from "./VideoPlayerModal";

const VIDEO_ACCEPT = {
  "video/*": [".mp4", ".avi", ".mkv", ".mov", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts", ".webm", ".flv", ".3gp"],
  "application/octet-stream": [".dav", ".h264", ".264"],
};

interface Props {
  caseId: number;
  videos: Video[];
  locations: Location[];
  jobs: Job[];
  canManage: boolean;
  onChange: () => void;
}

/** Nome da câmara editável no próprio quadro (vazio = nome do ficheiro). Guarda ao sair do campo ou com Enter. */
function CameraNameInput({ video, onSaved }: { video: Video; onSaved: () => void }) {
  const [value, setValue] = useState(video.camera_name);
  useEffect(() => setValue(video.camera_name), [video.camera_name]);
  const save = async () => {
    const name = value.trim();
    if (name === video.camera_name) return;
    try {
      await api.updateVideo(video.id, { camera_name: name });
      onSaved();
    } catch (e) {
      notifyError(e, "Não foi possível mudar o nome da câmara");
      setValue(video.camera_name);
    }
  };
  return (
    <TextInput size="xs" variant="filled" value={value} placeholder={video.filename} miw={160}
      aria-label={`Nome da câmara do vídeo #${video.id}`}
      onChange={(e) => setValue(e.currentTarget.value)} onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setValue(video.camera_name);
      }} />
  );
}

/** Cabeçalho de um local: nome (editável), n.º de câmaras e eliminação (só vazio). */
function LocationHeader({ location, cameras, duration, onChange }: {
  location: Location; cameras: number; duration: number; onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(location.name);
  const rename = async () => {
    try {
      await api.renameLocation(location.id, name.trim());
      setEditing(false);
      onChange();
    } catch (e) {
      notifyError(e, "Não foi possível mudar o nome do local");
    }
  };
  const remove = async () => {
    try {
      await api.deleteLocation(location.id);
      notifyOk(`Local "${location.name}" eliminado.`);
      onChange();
    } catch (e) {
      notifyError(e, "Não foi possível eliminar o local");
    }
  };
  return (
    <Group justify="space-between" wrap="nowrap">
      <Group gap="xs" wrap="nowrap">
        <IconMapPin size={20} color="var(--mantine-color-indigo-6)" />
        {editing ? (
          <Group gap={4} wrap="nowrap">
            <TextInput size="xs" value={name} data-autofocus aria-label="Nome do local"
              onChange={(e) => setName(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) rename(); if (e.key === "Escape") setEditing(false); }} />
            <ActionIcon size="sm" variant="light" color="green" aria-label="Guardar nome do local"
              disabled={!name.trim()} onClick={rename}><IconCheck size={14} /></ActionIcon>
            <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Cancelar"
              onClick={() => { setEditing(false); setName(location.name); }}><IconX size={14} /></ActionIcon>
          </Group>
        ) : (
          <>
            <Text fw={700} size="lg">{location.name}</Text>
            <Tooltip label="Mudar o nome do local">
              <ActionIcon size="sm" variant="subtle" aria-label={`Mudar o nome de ${location.name}`}
                onClick={() => { setName(location.name); setEditing(true); }}><IconPencil size={14} /></ActionIcon>
            </Tooltip>
          </>
        )}
        <Badge variant="light">{cameras} câmara{cameras === 1 ? "" : "s"}</Badge>
        {cameras > 0 && <Text size="xs" c="dimmed">{formatTime(duration)} de vídeo</Text>}
      </Group>
      <Tooltip label={cameras ? "Mova ou elimine os vídeos para poder eliminar o local" : "Eliminar o local"}>
        <span>
          <ActionIcon variant="subtle" color="red" disabled={cameras > 0} onClick={remove}
            aria-label={`Eliminar o local ${location.name}`}><IconTrash size={16} /></ActionIcon>
        </span>
      </Tooltip>
    </Group>
  );
}

export default function VideosPanel({ caseId, videos, locations, jobs, canManage, onChange }: Props) {
  const [progress, setProgress] = useState<{ location: number; value: number } | null>(null);
  const [clockVideo, setClockVideo] = useState<Video | null>(null);
  const [playId, setPlayId] = useState<number | null>(null);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [newName, setNewName] = useState<string | null>(null);

  // Atualiza o estado das conversões para o browser enquanto houver alguma na fila ou a decorrer.
  // (e enquanto a hora estiver a ser lida por OCR na importação).
  const converting = videos.some((v) => v.playback_status === "pending" || v.playback_status === "running"
    || v.clock_auto_status === "pending" || v.clock_auto_status === "running");
  useEffect(() => {
    if (!converting) return;
    const t = setInterval(onChange, 3000);
    return () => clearInterval(t);
  }, [converting, onChange]);

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

  const createCopy = async (v: Video) => {
    try {
      await api.createPlaybackCopy(v.id);
      notifyOk(`Cópia de reprodução do vídeo #${v.id} na fila.`);
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };

  const upload = async (location: Location, files: File[]) => {
    if (!files.length) return;
    setProgress({ location: location.id, value: 0 });
    try {
      const created = await api.uploadVideos(caseId, files, (value) => setProgress({ location: location.id, value }),
        location.id);
      notifyOk(`${created.length} vídeo(s) importado(s) para "${location.name}".`);
    } catch (e) {
      notifyError(e, "Falha ao importar vídeo");
    } finally {
      setProgress(null);
      onChange();
    }
  };

  const createLocation = async () => {
    if (!newName?.trim()) return;
    try {
      const loc = await api.createLocation(caseId, newName.trim());
      notifyOk(`Local "${loc.name}" criado. Importe os vídeos das câmaras desse local.`);
      setNewName(null);
      onChange();
    } catch (e) {
      notifyError(e, "Não foi possível criar o local");
    }
  };

  const move = async (v: Video, to: Location) => {
    try {
      await api.updateVideo(v.id, { location_id: to.id });
      notifyOk(`"${cameraName(v)}" movido para "${to.name}".`);
      onChange();
    } catch (e) {
      notifyError(e, "Não foi possível mover o vídeo");
    }
  };

  const groups = groupByLocation(videos, locations);

  return (
    <Stack>
      <Group justify="space-between">
        {newName === null ? (
          <Button leftSection={<IconMapPinPlus size={16} />} onClick={() => setNewName("")}>Novo local</Button>
        ) : (
          <Group gap="xs">
            <TextInput placeholder="Nome do local (ex.: Estação, Rua Direita n.º 10)" w={340} value={newName}
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
      {locations.length === 0 && (
        <Card withBorder>
          <Text c="dimmed">Ainda não há locais. Crie um local (ex.: "Estação") e importe para ele os vídeos das
            várias câmaras desse local.</Text>
        </Card>
      )}
      {groups.map(({ location, videos: vs }) => location && (
        <Card key={location.id} withBorder data-testid={`location-${location.id}`}>
          <Stack gap="sm">
            <LocationHeader location={location as Location} cameras={vs.length}
              duration={vs.reduce((n, v) => n + v.duration_s, 0)} onChange={onChange} />
            <UploadZone compact accept={VIDEO_ACCEPT} onDrop={(files) => upload(location as Location, files)}
              progress={progress?.location === location.id ? progress.value : null}
              icon={<IconMovie size={28} stroke={1.5} />}
              title={`Importar vídeos para "${location.name}"`}
              hint="Um vídeo por câmara/ângulo. Envio em blocos: se a ligação falhar, volte a largar o mesmo ficheiro." />
            {vs.length === 0 ? (
              <Text c="dimmed" size="sm">Sem câmaras neste local.</Text>
            ) : (
              <Table.ScrollContainer minWidth={1000}>
                <Table>
                  <Table.Thead>
                    <Table.Tr><Table.Th>#</Table.Th><Table.Th>Câmara</Table.Th><Table.Th>Tamanho</Table.Th>
                      <Table.Th>Duração</Table.Th><Table.Th>FPS</Table.Th><Table.Th>Resolução</Table.Th><Table.Th>Codec</Table.Th><Table.Th>Reprodução no browser</Table.Th><Table.Th>Hora real</Table.Th><Table.Th /></Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {vs.map((v) => (
                  <Table.Tr key={v.id}>
                    <Table.Td>{v.id}</Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Tooltip label="Ver o vídeo">
                          <ActionIcon variant="light" size="sm" aria-label={`Ver ${v.filename}`} onClick={() => setPlayId(v.id)}>
                            <IconPlayerPlay size={14} />
                          </ActionIcon>
                        </Tooltip>
                        <Stack gap={0}>
                          <CameraNameInput video={v} onSaved={onChange} />
                          <Text size="xs" c="dimmed" truncate maw={260} title={v.filename}>{v.filename}</Text>
                        </Stack>
                      </Group>
                    </Table.Td><Table.Td>{formatBytes(v.size_bytes)}</Table.Td>
                    <Table.Td>{formatTime(v.duration_s)}</Table.Td><Table.Td>{v.fps.toFixed(2)}</Table.Td>
                    <Table.Td>{v.width}×{v.height}</Table.Td><Table.Td>{v.codec || "—"}</Table.Td>
                    <Table.Td><PlaybackBadge video={v} onCreate={createCopy} /></Table.Td>
                    <Table.Td>
                      {v.dvr_start ? (
                        <Stack gap={2}>
                          <Text size="xs" ff="monospace">{realTime(v, 0)} → {realTime(v, v.duration_s)}</Text>
                          <Tooltip label={v.time_note || (v.time_source === "ocr" ? "Lida por OCR" : "Acerto manual")}
                            multiline w={260}>
                            <Badge size="xs" variant="light" color={v.time_source === "ocr" ? "teal" : "blue"}>
                              {v.time_source === "ocr" ? (v.clock_auto_status === "done" ? "OCR automático" : "OCR") : "manual"}
                              {v.clock_offset_s ? ` · desvio ${v.clock_offset_s > 0 ? "+" : ""}${v.clock_offset_s} s` : ""}
                            </Badge>
                          </Tooltip>
                        </Stack>
                      ) : v.clock_auto_status === "pending" || v.clock_auto_status === "running" ? (
                        <Group gap={6} wrap="nowrap">
                          <Loader size={12} />
                          <Text size="xs" c="dimmed">{v.clock_auto_status === "running" ? "A ler a hora (OCR)…" : "Leitura da hora em fila…"}</Text>
                        </Group>
                      ) : v.clock_auto_status === "failed" || v.clock_auto_status === "unavailable" ? (
                        <Tooltip multiline w={320} label={`${v.clock_auto_message || "Sem leitura."} Acerte a hora manualmente ou marque a zona da data/hora.`}>
                          <Badge size="xs" color="orange" variant="light">
                            {v.clock_auto_status === "unavailable" ? "OCR indisponível" : "OCR sem resultado"}
                          </Badge>
                        </Tooltip>
                      ) : <Text size="xs" c="dimmed">não acertada</Text>}
                      <Button size="compact-xs" variant="subtle" leftSection={<IconClock size={14} />} mt={4}
                        onClick={() => setClockVideo(v)}>Acertar hora</Button>
                    </Table.Td>
                    <Table.Td w={40}>
                      <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                          <ActionIcon variant="subtle" aria-label={`Ações do vídeo ${v.filename}`}><IconDots size={16} /></ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Label>Mover para outro local</Menu.Label>
                          {locations.filter((l) => l.id !== v.location_id).map((l) => (
                            <Menu.Item key={l.id} leftSection={<IconArrowsExchange size={14} />} onClick={() => move(v, l)}>
                              {l.name}
                            </Menu.Item>
                          ))}
                          {locations.length < 2 && <Menu.Item disabled>Crie outro local primeiro</Menu.Item>}
                          {canManage && <>
                            <Menu.Divider />
                            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => setDeleteId(v.id)}>
                              Eliminar vídeo
                            </Menu.Item>
                          </>}
                        </Menu.Dropdown>
                      </Menu>
                    </Table.Td>
                  </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </Stack>
        </Card>
      ))}
      {groups.filter((g) => !g.location).map((g) => (
        <Card key="sem-local" withBorder>
          <Text size="sm" c="orange">{g.videos.length} vídeo(s) sem local — recarregue a página.</Text>
        </Card>
      ))}
      <VideoPlayerModal key={playId ?? 0} video={videos.find((v) => v.id === playId) ?? null} onClose={() => setPlayId(null)}
        onChange={onChange} locations={locations} />
      <DeleteVideoModal video={videos.find((v) => v.id === deleteId) ?? null} jobs={jobs} locations={locations}
        onClose={() => setDeleteId(null)} onDeleted={() => { setDeleteId(null); onChange(); }} />
      {clockVideo && (
        <ClockModal key={clockVideo.id} video={clockVideo} opened onClose={() => setClockVideo(null)} locations={locations}
          onSaved={(v) => { setClockVideo(v); onChange(); }} />
      )}
    </Stack>
  );
}

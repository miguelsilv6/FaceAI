import {
  ActionIcon, Badge, Box, Button, Card, Chip, Group, SegmentedControl, SimpleGrid, Slider, Stack, Text, Tooltip,
} from "@mantine/core";
import {
  IconArrowsMaximize, IconArrowsMinimize, IconPlayerPause, IconPlayerPlay, IconPlayerSkipForward, IconRewindBackward10,
  IconRewindForward10,
} from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import { cameraLabel, cameraName, groupByLocation } from "../places";
import { VIDEO_KEYS_HINT, useVideoKeys } from "../useVideoKeys";
import type { Location, RouteSegment, Video } from "../types";

/** Hora "de parede" do DVR em segundos (sem fuso, tratada como UTC só para fazer contas). */
export function wallSeconds(iso: string): number {
  return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime() / 1000;
}

const wallOf = (v: Video, t: number) => wallSeconds(v.dvr_start!) + t * (v.dvr_rate || 1) + (v.clock_offset_s || 0);
const videoTimeAt = (v: Video, wall: number) => (wall - wallSeconds(v.dvr_start!) - (v.clock_offset_s || 0)) / (v.dvr_rate || 1);
const fmt = (wall: number) => new Date(wall * 1000).toISOString().slice(11, 19);
const MAX_DEFAULT = 9;

export interface SeekRequest {
  wall: number;
  n: number; // muda a cada pedido, para repetir o mesmo instante
}

/**
 * Várias câmaras lado a lado, sincronizadas pela hora real do DVR: um relógio comum comanda todos os vídeos.
 * Só visualização: a análise e as evidências usam sempre os ficheiros originais.
 */
export default function MultiCamPlayer({ videos, segments, seek, locations = [] }: {
  videos: Video[]; segments: RouteSegment[]; seek: SeekRequest | null; locations?: Location[];
}) {
  // Câmaras com hora acertada, pela ordem dos locais
  const camGroups = useMemo(() => groupByLocation(videos.filter((v) => v.dvr_start && v.duration_s > 0), locations)
    .filter((g) => g.videos.length), [videos, locations]);
  const cams = useMemo(() => camGroups.flatMap((g) => g.videos), [camGroups]);
  const [shown, setShown] = useState<string[]>([]);
  const [wall, setWall] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState("1");
  const [focus, setFocus] = useState<number | null>(null);
  const [broken, setBroken] = useState<Set<number>>(new Set());
  const els = useRef(new Map<number, HTMLVideoElement>());
  const wallRef = useRef(0);
  wallRef.current = wall;
  const holder = useRef<HTMLDivElement>(null);

  const t0 = cams.length ? Math.min(...cams.map((v) => wallOf(v, 0))) : 0;
  const t1 = cams.length ? Math.max(...cams.map((v) => wallOf(v, v.duration_s))) : 1;

  // Câmaras visíveis por omissão (as primeiras 9) e instante inicial: primeira aparição, senão o início.
  const camKey = cams.map((v) => v.id).join(",");
  useEffect(() => {
    setShown(cams.slice(0, MAX_DEFAULT).map((v) => String(v.id)));
    const first = segments.find((s) => s.real_start)?.real_start;
    setWall(first ? Math.max(t0, wallSeconds(first) - 2) : t0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camKey]);

  useEffect(() => {
    if (!seek) return;
    setWall(Math.min(t1, Math.max(t0, seek.wall)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seek]);

  // Relógio comum: avança em tempo real × velocidade enquanto reproduz.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      const next = wallRef.current + ((now - last) / 1000) * Number(speed);
      last = now;
      if (next >= t1) {
        setWall(t1);
        setPlaying(false);
        return;
      }
      setWall(next);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, t1]);

  // Cada vídeo segue o relógio: posição, velocidade e play/pausa; corrige o desvio quando passa a tolerância.
  useEffect(() => {
    for (const v of cams) {
      const el = els.current.get(v.id);
      if (!el) continue;
      const t = videoTimeAt(v, wall);
      if (t < 0 || t > v.duration_s) {
        if (!el.paused) el.pause();
        continue;
      }
      el.playbackRate = Math.min(16, Math.max(0.0625, Number(speed) / (v.dvr_rate || 1)));
      const drift = Math.abs(el.currentTime - t);
      if (playing) {
        if (drift > Math.max(0.4, 0.25 * Number(speed))) el.currentTime = t;
        if (el.paused) el.play().catch(() => undefined);
      } else {
        if (!el.paused) el.pause();
        if (drift > 0.04) el.currentTime = t;
      }
    }
  }, [wall, playing, speed, cams]);

  // Teclado: ← / → recuam/avançam 10 s no relógio comum; Espaço reproduz/pausa
  useVideoKeys({ anchor: holder, enabled: cams.length > 0,
    seek: (d) => setWall((w) => Math.min(t1, Math.max(t0, w + d))), toggle: () => setPlaying((p) => !p) });

  if (!cams.length) {
    return <Text c="dimmed" size="sm">Nenhum vídeo com hora real acertada: acerte o relógio no separador Locais e vídeos.</Text>;
  }

  const visible = cams.filter((v) => shown.includes(String(v.id)) && (focus == null || focus === v.id));
  const activeAt = (v: Video) => segments.filter((s) => s.video_id === v.id && s.real_start
    && wall >= wallSeconds(s.real_start) - 0.5 && wall <= wallSeconds(s.real_end ?? s.real_start) + 0.5);
  const nextSeg = segments.find((s) => s.real_start && wallSeconds(s.real_start) - 2 > wall + 0.5
    && shown.includes(String(s.video_id)));
  const jump = (d: number) => setWall((w) => Math.min(t1, Math.max(t0, w + d)));
  const cols = focus != null ? 1 : visible.length <= 1 ? 1 : visible.length <= 4 ? 2 : 3;

  return (
    <div ref={holder}>
    <Card withBorder aria-label="Leitor multi-câmara">
      <Stack gap="sm">
        <Group justify="space-between" wrap="wrap">
          <Text fw={600}>Câmaras sincronizadas pela hora real</Text>
          <Text size="xs" c="dimmed">Só visualização: a análise e as evidências usam sempre os ficheiros originais.</Text>
        </Group>
        {cams.length > 1 && (
          <Group gap={6} wrap="wrap" align="center">
            <Button.Group>
              <Button size="compact-xs" variant="default" onClick={() => setShown(cams.map((v) => String(v.id)))}>
                Todas
              </Button>
              <Button size="compact-xs" variant="default" onClick={() => setShown([])}>Nenhuma</Button>
              <Button size="compact-xs" variant="default"
                onClick={() => setShown(cams.map((v) => String(v.id)).filter((id) => !shown.includes(id)))}>
                Inverter
              </Button>
            </Button.Group>
            <Chip.Group multiple value={shown} onChange={setShown}>
              <Group gap="sm">
                {camGroups.map((g) => (
                  <Group key={g.location?.id ?? 0} gap={4} wrap="wrap">
                    {g.location && camGroups.length > 1 && (
                      <Tooltip label={`Mostrar só as câmaras de ${g.location.name}`}>
                        <Button size="compact-xs" variant="light" color="indigo"
                          onClick={() => setShown(g.videos.map((v) => String(v.id)))}>Só {g.location.name}</Button>
                      </Tooltip>
                    )}
                    {g.videos.map((v) => <Chip key={v.id} size="xs" value={String(v.id)}>{cameraName(v)}</Chip>)}
                  </Group>
                ))}
              </Group>
            </Chip.Group>
          </Group>
        )}
        <Group gap="xs" wrap="wrap">
          <Tooltip label="Recuar 10 s (←)">
            <ActionIcon variant="default" size="lg" aria-label="Recuar 10 segundos" onClick={() => jump(-10)}>
              <IconRewindBackward10 size={18} />
            </ActionIcon>
          </Tooltip>
          <ActionIcon size="lg" aria-label={playing ? "Pausa" : "Reproduzir"} onClick={() => setPlaying((p) => !p)}>
            {playing ? <IconPlayerPause size={18} /> : <IconPlayerPlay size={18} />}
          </ActionIcon>
          <Tooltip label="Avançar 10 s (→)">
            <ActionIcon variant="default" size="lg" aria-label="Avançar 10 segundos" onClick={() => jump(10)}>
              <IconRewindForward10 size={18} />
            </ActionIcon>
          </Tooltip>
          <Text ff="monospace" fw={700} miw={150}>
            {new Date(wall * 1000).toISOString().slice(0, 10)} {fmt(wall)}
          </Text>
          <SegmentedControl size="xs" value={speed} onChange={setSpeed}
            data={["0.5", "1", "2", "4", "8"].map((x) => ({ value: x, label: `${x}×` }))} />
          <Button size="xs" variant="light" leftSection={<IconPlayerSkipForward size={14} />} disabled={!nextSeg}
            onClick={() => nextSeg && setWall(wallSeconds(nextSeg.real_start!) - 2)}>Próxima aparição</Button>
          <Text size="xs" c="dimmed">{VIDEO_KEYS_HINT}</Text>
        </Group>
        <Slider min={t0} max={t1} step={0.1} value={wall} label={(x) => fmt(x)} aria-label="Hora real"
          onChange={(x) => { setPlaying(false); setWall(x); }}
          marks={segments.filter((s) => s.real_start).slice(0, 200).map((s) => ({ value: wallSeconds(s.real_start!) }))} />
        <Group justify="space-between">
          <Text size="xs" ff="monospace" c="dimmed">{fmt(t0)}</Text>
          <Text size="xs" ff="monospace" c="dimmed">{fmt(t1)}</Text>
        </Group>
        {!visible.length && <Text size="sm" c="dimmed">Nenhuma câmara selecionada.</Text>}
        <SimpleGrid cols={{ base: 1, sm: Math.min(2, cols), lg: cols }} spacing="xs">
          {visible.map((v) => {
            const t = videoTimeAt(v, wall);
            const inside = t >= 0 && t <= v.duration_s;
            const active = activeAt(v);
            const preparing = v.playback_status === "pending" || v.playback_status === "running";
            return (
              <Box key={v.id} p={4} style={{
                border: `2px solid ${active.length ? "var(--mantine-color-red-6)" : "var(--mantine-color-default-border)"}`,
                borderRadius: 6,
              }}>
                <Group justify="space-between" gap={4} wrap="nowrap" mb={4}>
                  <Text size="xs" fw={600} truncate="end" title={v.filename}>{cameraLabel(v, locations)}</Text>
                  <Group gap={4} wrap="nowrap">
                    {active.map((s, i) => <Badge key={i} size="xs" color="red">{s.person_label}</Badge>)}
                    <Tooltip label={focus === v.id ? "Ver todas" : "Ampliar esta câmara"}>
                      <ActionIcon size="sm" variant="subtle" aria-label={focus === v.id ? "Ver todas as câmaras" : `Ampliar ${cameraLabel(v, locations)}`}
                        onClick={() => setFocus((f) => (f === v.id ? null : v.id))}>
                        {focus === v.id ? <IconArrowsMinimize size={14} /> : <IconArrowsMaximize size={14} />}
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Group>
                <Box style={{ position: "relative", background: "#000", borderRadius: 4, aspectRatio: `${v.width || 16} / ${v.height || 9}` }}>
                  {preparing ? (
                    <Text c="gray.4" size="xs" p="sm">A preparar a cópia de reprodução deste vídeo…</Text>
                  ) : (
                    <video ref={(el) => { if (el) els.current.set(v.id, el); else els.current.delete(v.id); }}
                      src={api.videoUrl(v)} muted playsInline preload="auto"
                      onError={() => setBroken((b) => new Set(b).add(v.id))}
                      style={{ width: "100%", height: "100%", display: "block", objectFit: "contain", opacity: inside ? 1 : 0.15 }} />
                  )}
                  {!inside && !preparing && (
                    <Text c="gray.3" size="xs" ta="center" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
                      Sem gravação a esta hora
                    </Text>
                  )}
                  {broken.has(v.id) && (
                    <Text c="orange.3" size="xs" p="xs" style={{ position: "absolute", left: 0, right: 0, bottom: 0, background: "rgba(0,0,0,.7)" }}>
                      O browser não reproduz este vídeo: crie a cópia de reprodução nos resultados da análise.
                    </Text>
                  )}
                </Box>
                <Text size="xs" ff="monospace" c="dimmed" mt={2}>
                  {inside ? `vídeo ${new Date(Math.max(0, t) * 1000).toISOString().slice(11, 19)}` : "—"}
                </Text>
              </Box>
            );
          })}
        </SimpleGrid>
      </Stack>
    </Card>
    </div>
  );
}

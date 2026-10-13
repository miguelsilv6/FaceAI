import {
  Alert, Anchor, Breadcrumbs, Button, Card, Group, Image, Menu, Modal, Progress, SegmentedControl, Select, SimpleGrid,
  Slider, Stack, Switch, Table, Text, Title,
} from "@mantine/core";
import { IconAlertTriangle, IconDownload, IconInfoCircle, IconPlayerStop } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, formatTime } from "../api";
import DetectionCard from "../components/DetectionCard";
import TrackCard from "../components/TrackCard";
import JobStatusBadge from "../components/JobStatusBadge";
import Timeline from "../components/Timeline";
import { notifyError } from "../notify";
import type { Detection, Job, ReviewStatus, Segment, Track, Video } from "../types";

const EXPORTS = [
  ["pdf", "Relatório PDF"],
  ["html", "Relatório HTML"],
  ["csv", "Deteções (CSV)"],
  ["json", "Resultados (JSON)"],
  ["zip", "Pacote completo (ZIP: relatório, CSV, JSON, recortes, auditoria)"],
] as const;

export default function Results() {
  const jobId = Number(useParams().jobId);
  const [job, setJob] = useState<Job | null>(null);
  const [videos, setVideos] = useState<Video[]>([]);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [view, setView] = useState<"tracks" | "detections">("tracks");
  const [trackFilter, setTrackFilter] = useState<number | null>(null);
  const [videoId, setVideoId] = useState<number | null>(null);
  const [minSim, setMinSim] = useState(0);
  const [showCandidates, setShowCandidates] = useState(false);
  const [order, setOrder] = useState<"time" | "score" | "quality">("time");
  const [frameDet, setFrameDet] = useState<Detection | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);

  const loadResults = useCallback(() => {
    Promise.all([api.detections(jobId), api.segments(jobId), api.tracks(jobId)])
      .then(([d, s, t]) => {
        setDetections(d);
        setSegments(s);
        setTracks(t);
      })
      .catch(notifyError);
  }, [jobId]);

  useEffect(() => {
    api.job(jobId).then((j) => {
      setJob(j);
      api.videos(j.case_id).then((vs) => {
        const used = vs.filter((v) => j.video_ids.includes(v.id));
        setVideos(used);
        setVideoId((cur) => cur ?? used[0]?.id ?? null);
      });
    }).catch(notifyError);
    loadResults();
  }, [jobId, loadResults]);

  // Atualiza o estado e os resultados parciais enquanto a análise decorre.
  const running = job?.status === "running" || job?.status === "queued";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      api.job(jobId).then((j) => {
        setJob(j);
        if (j.status !== "running" && j.status !== "queued") loadResults();
      });
      loadResults();
    }, 2000);
    return () => clearInterval(t);
  }, [running, jobId, loadResults]);

  const video = videos.find((v) => v.id === videoId) ?? null;
  const trackById = useMemo(() => new Map(tracks.map((t) => [t.id, t])), [tracks]);
  // Uma deteção conta como correspondência se estiver abaixo do limiar ou pertencer a um track com correspondência.
  const isEffectiveMatch = (d: Detection) => d.is_match || (d.track_id != null && !!trackById.get(d.track_id)?.is_match);
  const videoDets = useMemo(() => detections.filter((d) => d.video_id === videoId), [detections, videoId]);
  const visible = useMemo(() => {
    const list = videoDets.filter((d) => (trackFilter == null || d.track_id === trackFilter)
      && (showCandidates || trackFilter != null || isEffectiveMatch(d)) && d.similarity >= minSim);
    if (order === "score") return [...list].sort((a, b) => b.similarity - a.similarity);
    if (order === "quality") return [...list].sort((a, b) => b.quality - a.quality);
    return list;
  }, [videoDets, showCandidates, minSim, order, trackFilter, trackById]);
  const visibleTracks = useMemo(() => {
    const list = tracks.filter((t) => t.video_id === videoId && (showCandidates || t.is_match)
      && 1 - t.track_distance >= minSim);
    if (order === "score") return [...list].sort((a, b) => a.track_distance - b.track_distance);
    if (order === "quality") return [...list].sort((a, b) => b.best_quality - a.best_quality);
    return list;
  }, [tracks, videoId, showCandidates, minSim, order]);
  const hasTracks = tracks.length > 0;
  const showTracks = hasTracks && view === "tracks" && trackFilter == null;
  const videoSegments = segments.filter((s) => s.video_id === videoId);

  const seek = (t: number) => {
    const el = videoRef.current;
    if (el) {
      el.currentTime = Math.max(0, t);
      el.pause();
    }
    setCurrentTime(t);
  };

  const review = async (det: Detection, status: ReviewStatus) => {
    try {
      const updated = await api.review(det.id, status);
      setDetections((ds) => ds.map((d) => (d.id === updated.id ? updated : d)));
      api.segments(jobId).then(setSegments);
    } catch (e) {
      notifyError(e);
    }
  };

  const reviewTrack = async (track: Track, status: ReviewStatus) => {
    try {
      const updated = await api.reviewTrack(track.id, status);
      setTracks((ts) => ts.map((t) => (t.id === updated.id ? updated : t)));
      loadResults();
    } catch (e) {
      notifyError(e);
    }
  };

  if (!job) return null;
  // Mesmo critério do relatório: correspondência no frame ou num track com correspondência.
  const counts = {
    confirmed: detections.filter((d) => isEffectiveMatch(d) && d.review_status === "confirmed").length,
    rejected: detections.filter((d) => isEffectiveMatch(d) && d.review_status === "rejected").length,
    matches: detections.filter(isEffectiveMatch).length,
    tracks: tracks.filter((t) => t.is_match).length,
  };

  return (
    <Stack maw={1400} mx="auto">
      <Breadcrumbs>
        <Anchor component={Link} to="/">Casos</Anchor>
        <Anchor component={Link} to={`/cases/${job.case_id}`}>Caso #{job.case_id}</Anchor>
        <Text>Análise #{job.id}</Text>
      </Breadcrumbs>

      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap="sm"><Title order={2}>Análise #{job.id}</Title><JobStatusBadge status={job.status} /></Group>
          <Text size="sm" c="dimmed">
            {job.params.model} + {job.params.detector} · limiar {job.params.threshold} · {job.params.sample_fps} fps ·
            {" "}{String(job.environment.engine ?? "")} {String(job.environment.device ?? "?")} ·{" "}
            {job.frames_sampled} frames amostrados ({job.frames_skipped_motion} sem movimento, {job.frames_skipped_noface} sem faces)
            · {job.frames_processed} com deteção completa · {job.faces_detected} faces ·{" "}
            {counts.matches} correspondências ({counts.confirmed} confirmadas, {counts.rejected} rejeitadas)
            {job.params.tracking ? ` · ${counts.tracks} tracks com correspondência` : ""}
          </Text>
        </div>
        <Group>
          {running && (
            <Button color="orange" variant="light" leftSection={<IconPlayerStop size={18} />}
              onClick={() => api.cancelJob(job.id).then(setJob).catch(notifyError)}>Cancelar</Button>
          )}
          <Menu position="bottom-end" withinPortal>
            <Menu.Target>
              <Button leftSection={<IconDownload size={18} />} disabled={running}>Exportar</Button>
            </Menu.Target>
            <Menu.Dropdown>
              {EXPORTS.map(([fmt, label]) => (
                <Menu.Item key={fmt} component="a" href={api.exportUrl(job.id, fmt)}>{label}</Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        </Group>
      </Group>

      {running && <Progress value={job.progress * 100} animated striped size="lg" />}
      {job.message && (
        <Alert color={job.status === "failed" ? "red" : "blue"}
          icon={job.status === "failed" ? <IconAlertTriangle size={18} /> : <IconInfoCircle size={18} />}>{job.message}</Alert>
      )}

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Stack gap="xs">
            {videos.length > 1 && (
              <Select label="Vídeo" value={videoId ? String(videoId) : null} allowDeselect={false}
                data={videos.map((v) => ({ value: String(v.id), label: `#${v.id} ${v.filename}` }))}
                onChange={(v) => v && setVideoId(Number(v))} />
            )}
            {video && (
              <>
                <video ref={videoRef} key={video.id} src={api.videoUrl(video.id)} controls preload="metadata"
                  style={{ width: "100%", maxHeight: 420, background: "#000", borderRadius: 6 }}
                  onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)} />
                <Text size="xs" c="dimmed">
                  Se o navegador não reproduzir este codec, use as imagens dos frames (botão 🔍 nas deteções) ou converta o vídeo para H.264/MP4.
                </Text>
                <Timeline duration={video.duration_s} segments={videoSegments} detections={videoDets.filter(isEffectiveMatch)}
                  currentTime={currentTime} onSeek={seek} />
              </>
            )}
          </Stack>
        </Card>
        <Card withBorder p={0}>
          <Text fw={600} p="sm">Segmentos de aparição ({videoSegments.length})</Text>
          {videoSegments.length === 0 ? (
            <Text c="dimmed" px="sm" pb="sm">{running ? "A analisar…" : "Nenhuma correspondência neste vídeo."}</Text>
          ) : (
            <Table.ScrollContainer minWidth={420} mah={460}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr><Table.Th>Melhor face</Table.Th><Table.Th>Início</Table.Th><Table.Th>Fim</Table.Th>
                    <Table.Th>Deteções</Table.Th><Table.Th>Melhor sim.</Table.Th></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {videoSegments.map((s, i) => (
                    <Table.Tr key={i} style={{ cursor: "pointer" }} onClick={() => seek(s.start_s)}>
                      <Table.Td><Image src={api.detectionImageUrl(s.best_detection_id, "crop")} w={48} h={48} radius="sm" /></Table.Td>
                      <Table.Td ff="monospace">{formatTime(s.start_s)}</Table.Td>
                      <Table.Td ff="monospace">{formatTime(s.end_s)}</Table.Td>
                      <Table.Td>{s.count}</Table.Td>
                      <Table.Td>{(s.best_similarity * 100).toFixed(1)}%</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Card>
      </SimpleGrid>

      <Card withBorder>
        <Group justify="space-between" mb="sm" wrap="wrap">
          <Group gap="sm">
            {hasTracks && (
              <SegmentedControl size="xs" value={view} onChange={(v) => { setView(v as "tracks" | "detections"); setTrackFilter(null); }}
                data={[{ value: "tracks", label: `Tracks (${visibleTracks.length})` }, { value: "detections", label: "Deteções" }]} />
            )}
            {!showTracks && <Text fw={600}>Deteções ({visible.length})</Text>}
            {trackFilter != null && (
              <Button size="compact-xs" variant="light" onClick={() => setTrackFilter(null)}>
                Track #{trackFilter} · mostrar tudo
              </Button>
            )}
          </Group>
          <Group gap="lg" wrap="wrap">
            <Stack gap={2} w={220}>
              <Text size="xs">Similaridade mínima: {(minSim * 100).toFixed(0)}%</Text>
              <Slider min={0} max={1} step={0.01} value={minSim} onChange={setMinSim} label={null} />
            </Stack>
            <Switch label="Mostrar candidatos abaixo do limiar" checked={showCandidates}
              onChange={(e) => setShowCandidates(e.currentTarget.checked)} />
            <SegmentedControl size="xs" value={order} onChange={(v) => setOrder(v as "time" | "score" | "quality")}
              data={[{ value: "time", label: "Tempo" }, { value: "score", label: "Semelhança" },
                { value: "quality", label: "Qualidade" }]} />
          </Group>
        </Group>
        {showTracks ? (
          visibleTracks.length === 0 ? (
            <Text c="dimmed">Sem tracks com os filtros atuais.</Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
              {visibleTracks.map((t) => (
                <TrackCard key={t.id} track={t} onSeek={seek} onReview={reviewTrack}
                  onShowDetections={(x) => { setTrackFilter(x.id); setView("detections"); }} />
              ))}
            </SimpleGrid>
          )
        ) : visible.length === 0 ? (
          <Text c="dimmed">Sem deteções com os filtros atuais.</Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
            {visible.map((d) => (
              <DetectionCard key={d.id} det={d} onSeek={(x) => seek(x.timestamp_s)} onReview={review} onOpenFrame={setFrameDet} />
            ))}
          </SimpleGrid>
        )}
      </Card>

      <Modal opened={!!frameDet} onClose={() => setFrameDet(null)} size="xl"
        title={frameDet ? `Deteção #${frameDet.id} · ${formatTime(frameDet.timestamp_s)} · frame ${frameDet.frame_index}` : ""}>
        {frameDet && <Image src={api.detectionImageUrl(frameDet.id, "frame")} alt="Frame completo" />}
      </Modal>
    </Stack>
  );
}

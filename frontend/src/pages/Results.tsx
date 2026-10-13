import {
  Alert, Anchor, Breadcrumbs, Button, Card, Group, Image, Menu, Modal, Progress, SegmentedControl, Select, SimpleGrid,
  Slider, Stack, Switch, Table, Text, Title,
} from "@mantine/core";
import {
  IconAlertTriangle, IconDownload, IconInfoCircle, IconPhoto, IconPlayerStop, IconTrash, IconUserPlus,
} from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, formatTime, realTime } from "../api";
import DeleteJobModal from "../components/DeleteJobModal";
import DetectionCard from "../components/DetectionCard";
import PersonCard from "../components/PersonCard";
import TrackCard from "../components/TrackCard";
import JobStatusBadge from "../components/JobStatusBadge";
import Timeline from "../components/Timeline";
import { notifyError, notifyOk } from "../notify";
import type { Detection, Job, Person, ReviewStatus, Segment, Track, Video } from "../types";

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
  const [persons, setPersons] = useState<Person[]>([]);
  const [view, setView] = useState<"persons" | "tracks" | "detections">("tracks");
  const [trackFilter, setTrackFilter] = useState<number | null>(null);
  const [personFilter, setPersonFilter] = useState<number | null>(null);
  const [videoId, setVideoId] = useState<number | null>(null);
  const [minSim, setMinSim] = useState(0);
  const [showCandidates, setShowCandidates] = useState(false);
  const [order, setOrder] = useState<"time" | "score" | "quality">("time");
  const [frameDet, setFrameDet] = useState<Detection | null>(null);
  const [frameVariant, setFrameVariant] = useState<"annotated" | "original">("annotated");
  const [deleting, setDeleting] = useState(false);
  const navigate = useNavigate();
  const [currentTime, setCurrentTime] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);

  const loadResults = useCallback(() => {
    Promise.all([api.detections(jobId), api.segments(jobId), api.tracks(jobId), api.persons(jobId)])
      .then(([d, s, t, p]) => {
        setDetections(d);
        setSegments(s);
        setTracks(t);
        setPersons(p);
      })
      .catch(notifyError);
  }, [jobId]);

  useEffect(() => {
    api.job(jobId).then((j) => {
      setJob(j);
      if (j.params.mode === "discover") setView("persons");
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
  const discover = job?.params.mode === "discover";
  const trackById = useMemo(() => new Map(tracks.map((t) => [t.id, t])), [tracks]);
  const personOf = (d: Detection) => (d.track_id != null ? trackById.get(d.track_id)?.person_id ?? null : null);
  const inPerson = (d: Detection) => personFilter == null || personOf(d) === personFilter;
  // Uma deteção conta como correspondência se estiver abaixo do limiar ou pertencer a um track com correspondência.
  const isEffectiveMatch = (d: Detection) => d.is_match || (d.track_id != null && !!trackById.get(d.track_id)?.is_match);
  const videoDets = useMemo(() => detections.filter((d) => d.video_id === videoId), [detections, videoId]);
  const visible = useMemo(() => {
    const list = videoDets.filter((d) => (trackFilter == null || d.track_id === trackFilter) && (discover
      ? inPerson(d)
      : (showCandidates || trackFilter != null || isEffectiveMatch(d)) && d.similarity >= minSim));
    if (order === "score") return [...list].sort((a, b) => b.similarity - a.similarity);
    if (order === "quality") return [...list].sort((a, b) => b.quality - a.quality);
    return list;
  }, [videoDets, showCandidates, minSim, order, trackFilter, trackById, discover, personFilter]);
  const visibleTracks = useMemo(() => {
    const list = tracks.filter((t) => t.video_id === videoId && (discover
      ? personFilter == null || t.person_id === personFilter
      : (showCandidates || t.is_match) && 1 - t.track_distance >= minSim));
    if (order === "score") return [...list].sort((a, b) => (discover ? b.n_faces - a.n_faces : a.track_distance - b.track_distance));
    if (order === "quality") return [...list].sort((a, b) => b.best_quality - a.best_quality);
    return list;
  }, [tracks, videoId, showCandidates, minSim, order, discover, personFilter]);
  const visiblePersons = useMemo(() => {
    if (order === "time") {
      return [...persons].sort((a, b) => a.first.video_id - b.first.video_id || a.first.start_s - b.first.start_s);
    }
    if (order === "quality") return [...persons].sort((a, b) => b.best_quality - a.best_quality);
    return persons; // por omissão: a pessoa 1 é a que mais aparece
  }, [persons, order]);
  const hasTracks = tracks.length > 0;
  const showPersons = discover && view === "persons" && trackFilter == null;
  const showTracks = hasTracks && view === "tracks" && trackFilter == null;
  const videoSegments = segments.filter((s) => s.video_id === videoId
    && (personFilter == null || s.person_id === personFilter));
  const videoById = useMemo(() => new Map(videos.map((v) => [v.id, v])), [videos]);

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

  const selectPerson = (p: Person) => {
    if (personFilter === p.person_id) {
      setPersonFilter(null);
      return;
    }
    setPersonFilter(p.person_id);
    const vid = videoId != null && p.video_ids.includes(videoId) ? videoId : p.first.video_id;
    setVideoId(vid);
    const start = segments.find((x) => x.person_id === p.person_id && x.video_id === vid)?.start_s ?? p.first.start_s;
    setTimeout(() => seek(start), 0);
  };

  const reviewPerson = async (p: Person, status: ReviewStatus) => {
    try {
      await api.reviewPerson(jobId, p.person_id, status);
      loadResults();
    } catch (e) {
      notifyError(e);
    }
  };

  const mergePerson = async (p: Person, into: Person) => {
    if (!window.confirm(`Juntar a pessoa #${p.person_id} com a pessoa #${into.person_id}? Fica registado na auditoria.`)) return;
    try {
      await api.mergePersons(jobId, [p.person_id, into.person_id]);
      setPersonFilter(null);
      loadResults();
      api.job(jobId).then(setJob);
    } catch (e) {
      notifyError(e);
    }
  };

  const moveTrack = async (t: Track, personId: number | null) => {
    try {
      const updated = await api.moveTrack(t.id, personId);
      notifyOk(`Track #${t.id} passou para a pessoa #${updated.person_id}.`);
      loadResults();
      api.job(jobId).then(setJob);
    } catch (e) {
      notifyError(e);
    }
  };

  const addAsReference = async (detectionId: number | null) => {
    if (detectionId == null) return;
    try {
      const ref = await api.detectionAsReference(detectionId);
      if (ref.valid) notifyOk(`Face adicionada às amostras do caso (${ref.filename}). Pode agora usá-la no modo "Procurar uma pessoa".`);
      else notifyError(new Error(`A face foi guardada mas não é uma amostra válida: ${ref.message}`));
    } catch (e) {
      notifyError(e);
    }
  };

  const openFrame = (detId: number) => {
    const d = detections.find((x) => x.id === detId);
    if (d) setFrameDet(d);
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
  const personIds = persons.map((p) => p.person_id);

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
            {discover
              ? `${persons.length} pessoas distintas · ${tracks.length} tracks · ${detections.length} faces guardadas (descoberta, sem amostra)`
              : <>{counts.matches} correspondências ({counts.confirmed} confirmadas, {counts.rejected} rejeitadas)
                {job.params.tracking ? ` · ${counts.tracks} tracks com correspondência` : ""}</>}
          </Text>
        </div>
        <Group>
          {running && (
            <Button color="orange" variant="light" leftSection={<IconPlayerStop size={18} />}
              onClick={() => api.cancelJob(job.id).then(setJob).catch(notifyError)}>Cancelar</Button>
          )}
          <Button color="red" variant="subtle" leftSection={<IconTrash size={18} />} disabled={running}
            onClick={() => setDeleting(true)}>Eliminar</Button>
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
                <Timeline duration={video.duration_s} segments={videoSegments}
                  detections={discover ? videoDets.filter(inPerson) : videoDets.filter(isEffectiveMatch)}
                  realTime={video.dvr_start ? (sec) => realTime(video, sec) : undefined}
                  currentTime={currentTime} onSeek={seek} />
              </>
            )}
          </Stack>
        </Card>
        <Card withBorder p={0}>
          <Group justify="space-between" p="sm">
            <Text fw={600}>
              {discover ? `Aparições${personFilter != null ? ` da pessoa #${personFilter}` : " (todas as pessoas)"}` : "Segmentos de aparição"}
              {" "}({videoSegments.length})
            </Text>
            {personFilter != null && (
              <Button size="compact-xs" variant="light" onClick={() => setPersonFilter(null)}>Todas as pessoas</Button>
            )}
          </Group>
          {videoSegments.length === 0 ? (
            <Text c="dimmed" px="sm" pb="sm">
              {running ? "A analisar…" : discover ? "Sem aparições neste vídeo." : "Nenhuma correspondência neste vídeo."}
            </Text>
          ) : (
            <Table.ScrollContainer minWidth={420} mah={460}>
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr><Table.Th>Melhor face</Table.Th><Table.Th>Início</Table.Th><Table.Th>Fim</Table.Th>
                    {discover ? <><Table.Th>Pessoa</Table.Th><Table.Th>Faces</Table.Th></>
                      : <><Table.Th>Deteções</Table.Th><Table.Th>Melhor sim.</Table.Th></>}
                    {video?.dvr_start && <Table.Th>Hora real</Table.Th>}</Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {videoSegments.map((s, i) => (
                    <Table.Tr key={i} style={{ cursor: "pointer" }} onClick={() => seek(s.start_s)}>
                      <Table.Td><Image src={api.detectionImageUrl(s.best_detection_id, "crop")} w={48} h={48} radius="sm" /></Table.Td>
                      <Table.Td ff="monospace">{formatTime(s.start_s)}</Table.Td>
                      <Table.Td ff="monospace">{formatTime(s.end_s)}</Table.Td>
                      {discover ? <><Table.Td>#{s.person_id}</Table.Td><Table.Td>{s.count}</Table.Td></>
                        : <><Table.Td>{s.count}</Table.Td><Table.Td>{(s.best_similarity * 100).toFixed(1)}%</Table.Td></>}
                      {video?.dvr_start && (
                        <Table.Td ff="monospace" fz="xs">{realTime(video, s.start_s)}<br />→ {realTime(video, s.end_s).slice(11)}</Table.Td>
                      )}
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
              <SegmentedControl size="xs" value={view}
                onChange={(v) => { setView(v as "persons" | "tracks" | "detections"); setTrackFilter(null); }}
                data={[...(discover ? [{ value: "persons", label: `Pessoas (${persons.length})` }] : []),
                  { value: "tracks", label: `Tracks (${visibleTracks.length})` },
                  { value: "detections", label: discover ? "Faces" : "Deteções" }]} />
            )}
            {!showTracks && !showPersons && <Text fw={600}>{discover ? "Faces" : "Deteções"} ({visible.length})</Text>}
            {discover && personFilter != null && !showPersons && (
              <Button size="compact-xs" variant="light" onClick={() => setPersonFilter(null)}>
                Pessoa #{personFilter} · mostrar todas
              </Button>
            )}
            {trackFilter != null && (
              <Button size="compact-xs" variant="light" onClick={() => setTrackFilter(null)}>
                Track #{trackFilter} · mostrar tudo
              </Button>
            )}
          </Group>
          <Group gap="lg" wrap="wrap">
            {!discover && (
              <>
                <Stack gap={2} w={220}>
                  <Text size="xs">Similaridade mínima: {(minSim * 100).toFixed(0)}%</Text>
                  <Slider min={0} max={1} step={0.01} value={minSim} onChange={setMinSim} label={null} />
                </Stack>
                <Switch label="Mostrar candidatos abaixo do limiar" checked={showCandidates}
                  onChange={(e) => setShowCandidates(e.currentTarget.checked)} />
              </>
            )}
            <SegmentedControl size="xs" value={order} onChange={(v) => setOrder(v as "time" | "score" | "quality")}
              data={[{ value: "time", label: "Tempo" }, { value: "score", label: discover ? "Presença" : "Semelhança" },
                { value: "quality", label: "Qualidade" }]} />
          </Group>
        </Group>
        {showPersons ? (
          visiblePersons.length === 0 ? (
            <Text c="dimmed">{running ? "As pessoas são agrupadas no fim da análise…" : "Não foram encontradas faces."}</Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
              {visiblePersons.map((p) => (
                <PersonCard key={p.person_id} person={p} selected={personFilter === p.person_id}
                  others={persons.filter((o) => o.person_id !== p.person_id)}
                  firstRealTime={realTime(videoById.get(p.first.video_id), p.first.start_s)}
                  onSelect={selectPerson} onReview={reviewPerson} onMerge={mergePerson}
                  onUseAsReference={(x) => addAsReference(x.best_detection_id)} />
              ))}
            </SimpleGrid>
          )
        ) : showTracks ? (
          visibleTracks.length === 0 ? (
            <Text c="dimmed">Sem tracks com os filtros atuais.</Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
              {visibleTracks.map((t) => (
                <TrackCard key={t.id} track={t} onSeek={seek} onReview={reviewTrack}
                  realStart={realTime(video, t.start_s)} realEnd={realTime(video, t.end_s)} onOpenFrame={openFrame}
                  personIds={discover ? personIds : undefined} onMove={discover ? moveTrack : undefined}
                  onShowDetections={(x) => { setTrackFilter(x.id); setView("detections"); }} />
              ))}
            </SimpleGrid>
          )
        ) : visible.length === 0 ? (
          <Text c="dimmed">Sem deteções com os filtros atuais.</Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
            {visible.map((d) => (
              <DetectionCard key={d.id} det={d} onSeek={(x) => seek(x.timestamp_s)} onReview={review} onOpenFrame={setFrameDet}
                realTime={realTime(video, d.timestamp_s)} personId={personOf(d)} />
            ))}
          </SimpleGrid>
        )}
      </Card>

      <DeleteJobModal job={deleting ? job : null} onClose={() => setDeleting(false)}
        onDeleted={(j) => navigate(`/cases/${j.case_id}`)} />

      <Modal opened={!!frameDet} onClose={() => setFrameDet(null)} size="xl"
        title={frameDet ? `Deteção #${frameDet.id} · ${formatTime(frameDet.timestamp_s)} · frame ${frameDet.frame_index}` : ""}>
        {frameDet && (
          <Stack gap="sm">
            <Group justify="space-between" wrap="wrap">
              <SegmentedControl size="xs" value={frameVariant}
                onChange={(v) => setFrameVariant(v as "annotated" | "original")}
                data={[{ value: "annotated", label: "Com marcação" }, { value: "original", label: "Original (sem marcação)" }]} />
              {(() => {
                const fv = videoById.get(frameDet.video_id);
                const rt = realTime(fv, frameDet.timestamp_s);
                return <Text size="xs" c="dimmed">{fv?.filename}{rt ? ` · hora real ${rt}` : ""}</Text>;
              })()}
            </Group>
            <Image key={`${frameDet.id}-${frameVariant}`} alt="Frame completo"
              src={api.detectionFrameUrl(frameDet.id, frameVariant)} />
            <Group justify="space-between" wrap="wrap">
              <Text size="xs" c="dimmed" maw={520}>
                O original é extraído do vídeo analisado (PNG, sem perdas) e verificado contra o recorte da face guardado na
                análise. Cada download fica registado na auditoria com o SHA-256 do ficheiro.
              </Text>
              <Group gap="xs">
                <Button component="a" size="xs" variant="light" leftSection={<IconDownload size={16} />}
                  href={api.detectionFrameUrl(frameDet.id, "annotated", true)}>Frame com marcação (JPEG)</Button>
                <Button component="a" size="xs" leftSection={<IconPhoto size={16} />}
                  href={api.detectionFrameUrl(frameDet.id, "original", true)}>Frame original (PNG)</Button>
                {discover && (
                  <Button size="xs" variant="subtle" leftSection={<IconUserPlus size={16} />}
                    onClick={() => addAsReference(frameDet.id)}>Usar face como amostra</Button>
                )}
              </Group>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

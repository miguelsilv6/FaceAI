import {
  ActionIcon, Alert, Anchor, Badge, Breadcrumbs, Button, Card, Chip, Group, Image, Menu, Modal, Progress, SegmentedControl, Select,
  SimpleGrid, Slider, Stack, Switch, Table, Text, Title, Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle, IconChevronLeft, IconChevronRight, IconDownload, IconInfoCircle, IconPhoto, IconPlayerStop, IconTrash,
} from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, browserCodecs, formatEta, formatTime, realTime } from "../api";
import DeleteJobModal from "../components/DeleteJobModal";
import BodyTrackCard from "../components/BodyTrackCard";
import DetectionCard from "../components/DetectionCard";
import EnhancePanel from "../components/EnhancePanel";
import NewPersonModal from "../components/NewPersonModal";
import PersonAssignMenu from "../components/PersonAssignMenu";
import PersonCard from "../components/PersonCard";
import SimilarModal, { type SimilarQuery } from "../components/SimilarModal";
import TrackCard from "../components/TrackCard";
import JobStatusBadge from "../components/JobStatusBadge";
import Timeline from "../components/Timeline";
import { notifyError, notifyOk } from "../notify";
import { personColor } from "../personColors";
import { VIDEO_KEYS_HINT, useHtmlVideoKeys } from "../useVideoKeys";
import type {
  CasePerson, Detection, Job, Location, Person, ReferenceImage, ReviewStatus, Segment, Track, Video, VideoProbe,
} from "../types";
import { cameraLabel, cameraName, groupByLocation } from "../places";

const EXPORTS = [
  ["pdf", "Relatório PDF"],
  ["html", "Relatório HTML"],
  ["csv", "Deteções (CSV)"],
  ["json", "Resultados (JSON)"],
  ["zip", "Pacote completo (ZIP: relatório, CSV, JSON, recortes, auditoria)"],
] as const;

export default function Results() {
  const jobId = Number(useParams().jobId);
  // ?video=&t=: abrir num vídeo e instante (ex.: a partir do percurso multi-câmara)
  const [searchParams] = useSearchParams();
  const startAt = useRef({ video: Number(searchParams.get("video")) || null, t: Number(searchParams.get("t")) || 0 });
  const [job, setJob] = useState<Job | null>(null);
  const [videos, setVideos] = useState<Video[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locFilter, setLocFilter] = useState<string>("all");
  const [detections, setDetections] = useState<Detection[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [tracks, setTracks] = useState<Track[]>([]);
  // Vestuário (corpos): separados das faces, para não mudar as contagens nem as vistas faciais.
  const [bodyTracks, setBodyTracks] = useState<Track[]>([]);
  const [bodyDets, setBodyDets] = useState<Detection[]>([]);
  const confirmedCount = [...detections, ...bodyDets].filter((d) => d.review_status === "confirmed").length;
  const [evidenceFilter, setEvidenceFilter] = useState<"all" | "face" | "vestuário">("all");
  const [persons, setPersons] = useState<Person[]>([]);
  const [view, setView] = useState<"persons" | "tracks" | "detections" | "clothing">("tracks");
  const [trackFilter, setTrackFilter] = useState<number | null>(null);
  const [personFilter, setPersonFilter] = useState<number | null>(null);
  const [videoId, setVideoId] = useState<number | null>(null);
  const [minSim, setMinSim] = useState(0);
  const [showCandidates, setShowCandidates] = useState(false);
  const [order, setOrder] = useState<"time" | "score" | "quality">("time");
  const [frameDet, setFrameDet] = useState<Detection | null>(null);
  const [similar, setSimilar] = useState<SimilarQuery | null>(null);
  // Pessoas do caso e fotos de referência criadas a partir de deteções ("Adicionar esta face à pessoa").
  const [casePersons, setCasePersons] = useState<CasePerson[]>([]);
  const [caseRefs, setCaseRefs] = useState<ReferenceImage[]>([]);
  const [newPersonFor, setNewPersonFor] = useState<number | null>(null);
  const [frameVariant, setFrameVariant] = useState<"annotated" | "original" | "enhanced">("annotated");
  const [deleting, setDeleting] = useState(false);
  // Erro de reprodução no browser (por vídeo), com o diagnóstico mostrado ao analista.
  const [playError, setPlayError] = useState<{ videoId: number; detail: string } | null>(null);
  const autoCopyRequested = useRef(new Set<string>());
  const navigate = useNavigate();
  const [currentTime, setCurrentTime] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  useHtmlVideoKeys(videoRef);

  const loadResults = useCallback(() => {
    Promise.all([api.detections(jobId), api.segments(jobId), api.tracks(jobId), api.persons(jobId)])
      .then(([d, s, t, p]) => {
        setDetections(d.filter((x) => x.kind !== "body"));
        setBodyDets(d.filter((x) => x.kind === "body"));
        setSegments(s);
        setTracks(t.filter((x) => x.kind !== "body"));
        setBodyTracks(t.filter((x) => x.kind === "body"));
        setPersons(p);
      })
      .catch(notifyError);
  }, [jobId]);

  useEffect(() => {
    api.job(jobId).then((j) => {
      setJob(j);
      if (j.params.mode === "discover") setView("persons");
      Promise.all([api.videos(j.case_id), api.locations(j.case_id)]).then(([vs, ls]) => {
        // Pela ordem dos locais (câmaras de cada local juntas)
        const used = groupByLocation(vs.filter((v) => j.video_ids.includes(v.id)), ls).flatMap((g) => g.videos);
        setLocations(ls);
        setVideos(used);
        const wanted = used.find((v) => v.id === startAt.current.video)?.id;
        setVideoId((cur) => cur ?? wanted ?? used[0]?.id ?? null);
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
  const converting = video?.playback_status === "pending" || video?.playback_status === "running";
  // Atualiza o estado da cópia de reprodução. Depende só do caso e dos vídeos (valores estáveis): o objeto `job`
  // é substituído a cada 2 s enquanto a análise decorre, o que reiniciava o intervalo antes de ele disparar.
  const caseId = job?.case_id;
  const jobVideoIds = job?.video_ids.join(",") ?? "";
  useEffect(() => {
    if (!converting || caseId == null) return;
    const ids = new Set(jobVideoIds.split(",").map(Number));
    const t = setInterval(() => {
      api.videos(caseId).then((vs) => {
        const byId = new Map(vs.filter((v) => ids.has(v.id)).map((v) => [v.id, v]));
        setVideos((cur) => cur.map((v) => byId.get(v.id) ?? v)); // mantém a ordem por local
      }).catch(() => undefined);
    }, 2000);
    return () => clearInterval(t);
  }, [converting, caseId, jobVideoIds]);
  // Cópia de reprodução de todos os vídeos do caso: se o browser não reproduz um, os outros do mesmo DVR também não.
  const createPlaybackCopy = async (format?: "mp4" | "webm") => {
    if (!video || !job) return;
    try {
      const updated = await api.createCaseCopies(job.case_id, format);
      const byId = new Map(updated.map((v) => [v.id, v]));
      setVideos((vs) => vs.map((v) => byId.get(v.id) ?? v));
      setPlayError(null);
    } catch (e) {
      notifyError(e);
    }
  };
  const MEDIA_ERRORS: Record<number, string> = {
    1: "carregamento interrompido", 2: "erro de rede", 3: "erro ao descodificar", 4: "formato não suportado",
  };
  const onVideoError = async (v: Video, err: MediaError | null) => {
    const codecs = browserCodecs();
    const probe = await api.probeVideo(v.id).catch(() => ({} as VideoProbe));
    const file = probe.codec_name
      ? `${probe.format_name} · ${probe.codec_name}${probe.profile ? ` ${probe.profile}` : ""} · ${probe.pix_fmt}`
      : v.codec || "?";
    const playing = v.playback_status === "ready" ? `cópia ${v.playback_format === "webm" ? "VP9/WebM" : "H.264/MP4"}`
      : "ficheiro original";
    setPlayError({
      videoId: v.id,
      detail: `Erro do browser: ${err ? `${MEDIA_ERRORS[err.code] ?? err.code}${err.message ? ` (${err.message})` : ""}` : "?"}`
        + ` · a reproduzir: ${playing} · original: ${file}`
        + ` · este browser reproduz H.264: ${codecs.h264 ? "sim" : "não"}, VP9: ${codecs.vp9 ? "sim" : "não"}`,
    });
    // Pedido automático (uma vez por vídeo e formato) da cópia que este browser consegue reproduzir.
    const want = codecs.h264 ? "mp4" : "webm";
    const have = v.playback_status === "ready" ? (v.playback_format || "mp4") : null;
    const key = `${v.id}-${want}`;
    if (have !== want && v.playback_status !== "failed" && v.playback_status !== "unavailable"
      && !autoCopyRequested.current.has(key)) {
      autoCopyRequested.current.add(key);
      createPlaybackCopy(want);
    }
  };
  const discover = job?.params.mode === "discover";
  const trackById = useMemo(() => new Map(tracks.map((t) => [t.id, t])), [tracks]);
  // Modo procurar com várias pessoas: n.º e nome de cada uma (job.params.persons).
  const searched = !discover && (job?.params.persons?.length ?? 0) > 1 ? job!.params.persons! : [];
  const labelOf = (pid: number | null | undefined) => searched.find((p) => p.id === pid)?.label;
  const personOf = (d: Detection) => d.person_id
    ?? (d.track_id != null ? trackById.get(d.track_id)?.person_id ?? null : null);
  const inPerson = (d: Detection) => personFilter == null || personOf(d) === personFilter;
  // Uma deteção conta como correspondência se estiver abaixo do limiar ou pertencer a um track com correspondência.
  const isEffectiveMatch = (d: Detection) => d.is_match || (d.track_id != null && !!trackById.get(d.track_id)?.is_match);
  const videoDets = useMemo(() => detections.filter((d) => d.video_id === videoId), [detections, videoId]);
  // Correspondências por vídeo (o mesmo critério dos contadores do cabeçalho; no modo descoberta, todas as faces)
  // — para o filtro "só vídeos com deteções".
  const countByVideo = new Map<number, number>();
  for (const d of detections) {
    if (discover || isEffectiveMatch(d)) countByVideo.set(d.video_id, (countByVideo.get(d.video_id) ?? 0) + 1);
  }
  for (const t of bodyTracks) {
    if (t.evidence === "vestuário") countByVideo.set(t.video_id, (countByVideo.get(t.video_id) ?? 0) + 1);
  }
  const [onlyWithDets, setOnlyWithDets] = useState(false);
  const usedLocations = locations.filter((l) => videos.some((v) => v.location_id === l.id));
  const locVideos = locFilter === "all" ? videos : videos.filter((v) => String(v.location_id) === locFilter);
  const withDets = locVideos.filter((v) => (countByVideo.get(v.id) ?? 0) > 0);
  const navVideos = onlyWithDets && withDets.length ? withDets : locVideos;
  const navKey = navVideos.map((v) => v.id).join(",");
  useEffect(() => {
    // O vídeo atual saiu da lista filtrada: passa para o primeiro com deteções.
    if (navVideos.length && !navVideos.some((v) => v.id === videoId)) setVideoId(navVideos[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onlyWithDets, navKey]);
  const videoIndex = navVideos.findIndex((v) => v.id === videoId);
  const goToVideo = (delta: number) => {
    const next = navVideos[videoIndex + delta];
    if (next) {
      setVideoId(next.id);
      setCurrentTime(0);
    }
  };
  const visible = useMemo(() => {
    const list = videoDets.filter((d) => (trackFilter == null || d.track_id === trackFilter) && (discover
      ? inPerson(d)
      : (showCandidates || trackFilter != null || isEffectiveMatch(d)) && d.similarity >= minSim && inPerson(d)));
    if (order === "score") return [...list].sort((a, b) => b.similarity - a.similarity);
    if (order === "quality") return [...list].sort((a, b) => b.quality - a.quality);
    return list;
  }, [videoDets, showCandidates, minSim, order, trackFilter, trackById, discover, personFilter]);
  const visibleTracks = useMemo(() => {
    const list = tracks.filter((t) => t.video_id === videoId && (discover
      ? personFilter == null || t.person_id === personFilter
      : (showCandidates || t.is_match) && 1 - t.track_distance >= minSim
        && (personFilter == null || t.person_id === personFilter)));
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
  const clothing = !!job?.params.clothing;
  const videoSegments = segments.filter((s) => s.video_id === videoId
    && (personFilter == null || s.person_id === personFilter)
    && (evidenceFilter === "all" || (s.evidence ?? "face").includes(evidenceFilter)));
  const sortedBodyTracks = useMemo(() => [...bodyTracks].sort((a, b) =>
    Number(a.evidence !== "vestuário") - Number(b.evidence !== "vestuário") || a.video_id - b.video_id
    || a.start_s - b.start_s), [bodyTracks]);
  const seekBody = (t: Track) => {
    if (t.video_id !== videoId) setVideoId(t.video_id);
    setTimeout(() => seek(t.start_s), 0);
  };
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

  const loadCasePeople = useCallback((caseId: number) => {
    Promise.all([api.casePersons(caseId), api.references(caseId)])
      .then(([p, r]) => { setCasePersons(p); setCaseRefs(r); }).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (job?.case_id != null) loadCasePeople(job.case_id);
  }, [job?.case_id, loadCasePeople]);

  const addToPerson = async (detectionId: number | null, personId: number | "new" | null = null) => {
    if (detectionId == null || !job) return;
    if (personId === "new") {
      setNewPersonFor(detectionId);
      return;
    }
    try {
      const ref = await api.detectionAsReference(detectionId, personId);
      const who = casePersons.find((p) => p.id === personId)?.name;
      if (ref.valid) notifyOk(`Face adicionada ${who ? `às fotos de ${who}` : "às fotos soltas"}. Será usada nas próximas análises.`);
      else notifyError(new Error(`A face foi guardada mas não é uma foto de referência válida: ${ref.message}`));
      loadCasePeople(job.case_id);
    } catch (e) {
      notifyError(e);
    }
  };
  const addAsReference = (detectionId: number | null) => addToPerson(detectionId, null);
  const removeFromPerson = async (ref: ReferenceImage) => {
    if (!job) return;
    try {
      const r = await api.removeFromPerson(ref.id);
      notifyOk(r.action === "deleted" ? "Face removida das fotos de referência."
        : "A face já foi usada numa análise: saiu da pessoa e ficou nas fotos soltas.");
      loadCasePeople(job.case_id);
    } catch (e) {
      notifyError(e);
    }
  };
  const refByDetection = new Map(caseRefs.filter((r) => r.source_detection_id != null).map((r) => [r.source_detection_id!, r]));
  const assignFor = (detId: number | null | undefined) => (detId == null ? undefined : {
    persons: casePersons, existing: refByDetection.get(detId), onAdd: addToPerson, onRemove: removeFromPerson,
  });

  const openFrame = (detId: number) => {
    const d = detections.find((x) => x.id === detId) ?? bodyDets.find((x) => x.id === detId);
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
                {job.params.tracking ? ` · ${counts.tracks} tracks com correspondência` : ""}
                {clothing && !running ? ` · vestuário: ${job.clothing_samples} amostra(s), ${bodyTracks.filter((t) => t.evidence === "vestuário").length} aparição(ões) só pela roupa` : ""}</>}
            {running && (
              <Text span size="sm" fw={600} c="blue"> · {Math.round(job.progress * 100)}% · {formatEta(job.eta_s)}</Text>
            )}
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
              <Menu.Divider />
              <Menu.Item component="a" href={api.validatedFramesUrl("jobs", job.id)} disabled={!confirmedCount}
                title="Frames completos do vídeo original, sem anotações, de todas as deteções confirmadas (com manifesto e SHA-256)">
                Frames originais validados (ZIP · {confirmedCount})
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </Group>

      {running && <Progress value={job.progress * 100} animated striped size="lg" />}
      {clothing && !running && job.clothing_summary?.note && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />}>{job.clothing_summary.note}</Alert>
      )}
      {job.message && (
        <Alert color={job.status === "failed" ? "red" : "blue"}
          icon={job.status === "failed" ? <IconAlertTriangle size={18} /> : <IconInfoCircle size={18} />}>{job.message}</Alert>
      )}

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Stack gap="xs">
            {videos.length > 1 && (
              <Group gap="xs" align="flex-end" wrap="nowrap">
                <Tooltip label="Vídeo anterior">
                  <ActionIcon variant="default" size="lg" aria-label="Vídeo anterior" disabled={videoIndex <= 0}
                    onClick={() => goToVideo(-1)}><IconChevronLeft size={18} /></ActionIcon>
                </Tooltip>
                <Select label={`Vídeo (${videoIndex + 1} de ${navVideos.length})`} style={{ flex: 1 }}
                  value={videoId ? String(videoId) : null} allowDeselect={false}
                  data={groupByLocation(navVideos, locations).filter((g) => g.videos.length).map((g) => ({
                    group: g.location ? g.location.name : "Sem local",
                    items: g.videos.map((v) => ({ value: String(v.id),
                      label: `${g.location ? `${g.location.name} · ` : ""}${cameraName(v)} · ${countByVideo.get(v.id) ?? 0} ${discover ? "faces" : "deteções"}` })),
                  }))}
                  onChange={(v) => v && setVideoId(Number(v))} />
                <Tooltip label="Vídeo seguinte">
                  <ActionIcon variant="default" size="lg" aria-label="Vídeo seguinte"
                    disabled={videoIndex < 0 || videoIndex >= navVideos.length - 1}
                    onClick={() => goToVideo(1)}><IconChevronRight size={18} /></ActionIcon>
                </Tooltip>
              </Group>
            )}
            {usedLocations.length > 1 && (
              <Chip.Group multiple={false} value={locFilter} onChange={(v) => setLocFilter(v as string)}>
                <Group gap={4}>
                  <Chip size="xs" value="all">Todos os locais</Chip>
                  {usedLocations.map((l) => {
                    const n = videos.filter((v) => v.location_id === l.id)
                      .reduce((acc, v) => acc + (countByVideo.get(v.id) ?? 0), 0);
                    return <Chip key={l.id} size="xs" value={String(l.id)} color="indigo">{l.name} ({n})</Chip>;
                  })}
                </Group>
              </Chip.Group>
            )}
            {videos.length > 1 && !running && (
              <Switch size="xs" checked={onlyWithDets} onChange={(e) => setOnlyWithDets(e.currentTarget.checked)}
                disabled={!withDets.length}
                label={`Só vídeos com ${discover ? "faces" : "deteções"} (${withDets.length} de ${locVideos.length})`} />
            )}
            {video && videos.length <= 1 && (
              <Text size="sm" fw={600}>{cameraLabel(video, locations)}</Text>
            )}
            {video && (
              <>
                {converting ? (
                  <Alert color="blue" icon={<IconInfoCircle size={18} />}>
                    O browser não reproduz o formato original deste vídeo ({video.codec || "codec desconhecido"}). A criar uma
                    cópia {video.playback_format === "webm" ? "VP9/WebM" : "H.264"} só para visualização:{" "}
                    {video.playback_status === "running" ? `${Math.round(video.playback_progress * 100)}%` : "em fila"}.
                    A análise e os resultados usam sempre o original.
                    <Progress mt="xs" value={video.playback_progress * 100} animated striped />
                  </Alert>
                ) : (
                  <video ref={videoRef} key={`${video.id}-${video.playback_status}-${video.playback_format}`} src={api.videoUrl(video)} controls
                    preload="metadata" style={{ width: "100%", maxHeight: 420, background: "#000", borderRadius: 6 }}
                    onError={(e) => onVideoError(video, e.currentTarget.error)}
                    onLoadedData={(e) => {
                      setPlayError(null);
                      if (startAt.current.video === video.id && startAt.current.t) { // instante pedido no URL (uma vez)
                        e.currentTarget.currentTime = startAt.current.t;
                        setCurrentTime(startAt.current.t);
                        startAt.current = { video: null, t: 0 };
                      }
                    }}
                    onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)} />
                )}
                {!converting && (playError?.videoId === video.id || video.playback_status === "failed"
                  || video.playback_status === "unavailable") ? (
                  <Alert color="orange" icon={<IconAlertTriangle size={18} />}>
                    {video.playback_status === "failed" ? `A conversão para o browser falhou: ${video.playback_error}. `
                      : video.playback_status === "unavailable" ? `${video.playback_error} `
                        : "O browser não consegue reproduzir este vídeo. "}
                    Pode criar uma cópia só para visualização; entretanto use os frames (botão 🔍 nas deteções).
                    {video.playback_status !== "unavailable" && (
                      <Group gap="xs" mt="xs">
                        <Button size="compact-xs" variant="light" onClick={() => createPlaybackCopy("mp4")}>
                          Criar cópias H.264 (MP4) de todos os vídeos
                        </Button>
                        <Button size="compact-xs" variant="light" onClick={() => createPlaybackCopy("webm")}>
                          Criar cópias VP9 (WebM) de todos os vídeos
                        </Button>
                      </Group>
                    )}
                    {playError?.videoId === video.id && (
                      <Text size="xs" c="dimmed" mt="xs" ff="monospace">{playError.detail}</Text>
                    )}
                  </Alert>
                ) : (
                  <Text size="xs" c="dimmed">
                    {video.playback_status === "ready"
                      ? `Reprodução a partir de uma cópia ${video.playback_format === "webm" ? "VP9" : "H.264"} (só visualização). ` : ""}
                    Também pode ver os frames de cada deteção (botão 🔍).
                  </Text>
                )}
                <Timeline duration={video.duration_s} segments={videoSegments}
                  detections={discover ? videoDets.filter(inPerson) : videoDets.filter(isEffectiveMatch)}
                  realTime={video.dvr_start ? (sec) => realTime(video, sec) : undefined}
                  currentTime={currentTime} onSeek={seek} />
                {!converting && <Text size="xs" c="dimmed" ta="right">{VIDEO_KEYS_HINT}</Text>}
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
            {clothing && (
              <SegmentedControl size="xs" value={evidenceFilter}
                onChange={(v) => setEvidenceFilter(v as "all" | "face" | "vestuário")}
                data={[{ value: "all", label: "Todas" }, { value: "face", label: "Face" }, { value: "vestuário", label: "Vestuário" }]} />
            )}
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
                  <Table.Tr><Table.Th>Melhor face</Table.Th>{clothing && <Table.Th>Evidência</Table.Th>}
                    <Table.Th>Início</Table.Th><Table.Th>Fim</Table.Th>
                    {discover ? <><Table.Th>Pessoa</Table.Th><Table.Th>Faces</Table.Th></>
                      : <><Table.Th>Deteções</Table.Th><Table.Th>Melhor sim.</Table.Th></>}
                    {video?.dvr_start && <Table.Th>Hora real</Table.Th>}</Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {videoSegments.map((s, i) => (
                    <Table.Tr key={i} style={{ cursor: "pointer" }} onClick={() => seek(s.start_s)}>
                      <Table.Td><Image src={api.detectionImageUrl(s.best_detection_id, "crop")} w={48} h={48} radius="sm" /></Table.Td>
                      {clothing && (
                        <Table.Td><Badge size="xs" variant="light"
                          color={s.evidence === "vestuário" ? "grape" : s.evidence === "face+vestuário" ? "orange" : "red"}>
                          {s.evidence ?? "face"}</Badge></Table.Td>
                      )}
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
            {(hasTracks || clothing) && (
              <SegmentedControl size="xs" value={view}
                onChange={(v) => { setView(v as "persons" | "tracks" | "detections" | "clothing"); setTrackFilter(null); }}
                data={[...(discover ? [{ value: "persons", label: `Pessoas (${persons.length})` }] : []),
                  { value: "tracks", label: `Tracks (${visibleTracks.length})` },
                  { value: "detections", label: discover ? "Faces" : "Deteções" },
                  ...(clothing ? [{ value: "clothing", label: `Vestuário (${bodyTracks.filter((t) => t.evidence === "vestuário").length})` }] : [])]} />
            )}
            {!showTracks && !showPersons && view !== "clothing" && <Text fw={600}>{discover ? "Faces" : "Deteções"} ({visible.length})</Text>}
            {searched.length > 0 && view !== "clothing" && (
              <Chip.Group multiple={false} value={personFilter == null ? "all" : String(personFilter)}
                onChange={(v) => setPersonFilter(v === "all" ? null : Number(v))}>
                <Group gap={4}>
                  <Chip size="xs" value="all">Todas as pessoas</Chip>
                  {searched.map((p) => (
                    <Chip key={p.id} size="xs" value={String(p.id)} color={personColor(p.id)}>{p.label}</Chip>
                  ))}
                </Group>
              </Chip.Group>
            )}
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
            {!discover && view !== "clothing" && (
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
        {view === "clothing" && clothing ? (
          <Stack gap="xs">
            <Alert color="grape" variant="light" icon={<IconInfoCircle size={18} />}>
              <b>Evidência complementar.</b> As amostras são o corpo da pessoa nos frames em que a face teve
              correspondência (tronco {job.clothing_summary?.gallery_colors?.tronco ?? "?"}, pernas{" "}
              {job.clothing_summary?.gallery_colors?.pernas ?? "?"}). As aparições "só vestuário" foram encontradas pela
              roupa noutras câmaras/momentos e têm de ser revistas: roupa semelhante (fardas, roupa escura) pode ser de
              outra pessoa. Limiar ReID usado: {job.clothing_summary?.calibration?.threshold_used ?? job.params.reid_threshold}
              {job.clothing_summary?.excluded_by_window ? ` · ${job.clothing_summary.excluded_by_window} excluído(s) pela janela temporal` : ""}.
            </Alert>
            {sortedBodyTracks.length === 0 ? (
              <Text c="dimmed">{running ? "O vestuário é comparado no fim da análise…" : "Sem amostras nem correspondências por vestuário."}</Text>
            ) : (
              <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
                {sortedBodyTracks.map((t) => (
                  <BodyTrackCard key={t.id} track={t} video={videoById.get(t.video_id)}
                    cameraLabel={cameraLabel(videoById.get(t.video_id), locations)}
                    realStart={realTime(videoById.get(t.video_id), t.start_s)} onSeek={seekBody}
                    onReview={reviewTrack} onOpenFrame={openFrame} />
                ))}
              </SimpleGrid>
            )}
          </Stack>
        ) : showPersons ? (
          visiblePersons.length === 0 ? (
            <Text c="dimmed">{running ? "As pessoas são agrupadas no fim da análise…" : "Não foram encontradas faces."}</Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
              {visiblePersons.map((p) => (
                <PersonCard key={p.person_id} person={p} selected={personFilter === p.person_id}
                  others={persons.filter((o) => o.person_id !== p.person_id)}
                  firstRealTime={realTime(videoById.get(p.first.video_id), p.first.start_s)}
                  onSelect={selectPerson} onReview={reviewPerson} onMerge={mergePerson}
                  onUseAsReference={(x) => addAsReference(x.best_detection_id)}
                  onFindSimilar={job.status === "completed"
                    ? (x) => setSimilar({ kind: "person", jobId: job.id, personId: x.person_id }) : undefined} />
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
                  personLabel={labelOf(t.person_id)} personAssign={assignFor(t.best_detection_id)}
                  onFindSimilar={job.status === "completed" ? (x) => setSimilar({ kind: "track", trackId: x.id }) : undefined}
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
                realTime={realTime(video, d.timestamp_s)} personId={personOf(d)} personLabel={labelOf(personOf(d))}
                personAssign={assignFor(d.id)} />
            ))}
          </SimpleGrid>
        )}
      </Card>

      <NewPersonModal opened={newPersonFor != null} onClose={() => setNewPersonFor(null)}
        onCreate={async (name) => {
          if (!job || newPersonFor == null) return;
          try {
            const p = await api.createPerson(job.case_id, name);
            setCasePersons((ps) => [...ps, p]);
            const det = newPersonFor;
            setNewPersonFor(null);
            const ref = await api.detectionAsReference(det, p.id);
            notifyOk(ref.valid ? `Pessoa "${p.name}" criada com esta face.` : `Pessoa criada, mas a face não é válida: ${ref.message}`);
            loadCasePeople(job.case_id);
          } catch (e) {
            notifyError(e);
          }
        }} />
      <SimilarModal query={similar} onClose={() => setSimilar(null)}
        defaultThreshold={Math.min(0.9, Math.max(0.05, Number(job.params.threshold ?? 0.5)))} />
      <DeleteJobModal job={deleting ? job : null} onClose={() => setDeleting(false)}
        onDeleted={(j) => navigate(`/cases/${j.case_id}`)} />

      <Modal opened={!!frameDet} onClose={() => setFrameDet(null)} size="xl"
        title={frameDet ? `Deteção #${frameDet.id} · ${formatTime(frameDet.timestamp_s)} · frame ${frameDet.frame_index}` : ""}>
        {frameDet && (
          <Stack gap="sm">
            <Group justify="space-between" wrap="wrap">
              <SegmentedControl size="xs" value={frameVariant}
                onChange={(v) => setFrameVariant(v as "annotated" | "original" | "enhanced")}
                data={[{ value: "annotated", label: "Com marcação" }, { value: "original", label: "Original (sem marcação)" },
                  { value: "enhanced", label: "Melhorar (revisão)" }]} />
              {(() => {
                const fv = videoById.get(frameDet.video_id);
                const rt = realTime(fv, frameDet.timestamp_s);
                return <Text size="xs" c="dimmed">{cameraLabel(fv, locations)}{fv && cameraName(fv) !== fv.filename ? ` (${fv.filename})` : ""}{rt ? ` · hora real ${rt}` : ""}</Text>;
              })()}
            </Group>
            {frameVariant === "enhanced" ? <EnhancePanel detectionId={frameDet.id} /> : (
              <Image key={`${frameDet.id}-${frameVariant}`} alt="Frame completo"
                src={api.detectionFrameUrl(frameDet.id, frameVariant)} />
            )}
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
                {frameDet.kind !== "body" && (
                  <PersonAssignMenu detectionId={frameDet.id} compact={false} {...assignFor(frameDet.id)!} />
                )}
              </Group>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

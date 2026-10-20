import {
  ActionIcon, Alert, Anchor, Badge, Breadcrumbs, Button, Card, Chip, Group, Image, Menu, Modal, Progress, SegmentedControl, Select,
  SimpleGrid, Stack, Switch, Table, Text, Title, Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle, IconChevronLeft, IconChevronRight, IconDeviceFloppy, IconDownload, IconInfoCircle, IconPhoto,
  IconPlayerStop, IconRefresh, IconTrash,
} from "@tabler/icons-react";
import { type ComponentProps, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, browserCodecs, formatTime, realTime } from "../api";
import AlertsCard from "../components/AlertsCard";
import DeleteJobModal from "../components/DeleteJobModal";
import BodyTrackCard from "../components/BodyTrackCard";
import DetectionCard from "../components/DetectionCard";
import EnhancePanel from "../components/EnhancePanel";
import NewPersonModal from "../components/NewPersonModal";
import PersonAssignMenu from "../components/PersonAssignMenu";
import PersonCard from "../components/PersonCard";
import SimilarModal, { type SimilarQuery } from "../components/SimilarModal";
import ResultsFilters, { type ReviewFilter } from "../components/ResultsFilters";
import ResultsSummary from "../components/ResultsSummary";
import TrackCard from "../components/TrackCard";
import JobStatusBadge from "../components/JobStatusBadge";
import Timeline from "../components/Timeline";
import { notifyError, notifyOk, notifyUndo } from "../notify";
import { personColor } from "../personColors";
import { usePolling } from "../usePolling";
import { VIDEO_KEYS_HINT, useHtmlVideoKeys } from "../useVideoKeys";
import type {
  CasePerson, Detection, Job, Location, Person, ReferenceImage, ReviewStatus, Segment, Track, Video, VideoProbe,
} from "../types";
import { cameraLabel, cameraName, groupByLocation } from "../places";

const PAGE = 240;

/** "Mostrar mais" no fim de uma grelha paginada. */
function ShowMore({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (total <= shown) return null;
  return (
    <Group justify="center" mt="sm">
      <Button variant="light" onClick={onMore}>Mostrar mais ({total - shown} de {total})</Button>
    </Group>
  );
}

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
  const [playDetails, setPlayDetails] = useState(false);
  const [playError, setPlayError] = useState<{ videoId: number; detail: string } | null>(null);
  const autoCopyRequested = useRef(new Set<string>());
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement>(null);
  useHtmlVideoKeys(videoRef);
  const [savingFrame, setSavingFrame] = useState(false);

  // Maior id de deteção já recebido: durante a análise só se pedem as novas (after_id).
  const lastDetId = useRef(0);
  const lastAux = useRef(0);
  const applyAux = useCallback(([s, t, p]: [Segment[], Track[], Person[]]) => {
    setSegments(s);
    setTracks(t.filter((x) => x.kind !== "body"));
    setBodyTracks(t.filter((x) => x.kind === "body"));
    setPersons(p);
  }, []);
  const loadResults = useCallback(() => {
    Promise.all([api.detections(jobId), api.segments(jobId), api.tracks(jobId), api.persons(jobId)])
      .then(([d, s, t, p]) => {
        lastDetId.current = d.reduce((m, x) => Math.max(m, x.id), 0);
        lastAux.current = Date.now();
        setDetections(d.filter((x) => x.kind !== "body"));
        setBodyDets(d.filter((x) => x.kind === "body"));
        applyAux([s, t, p]);
      })
      .catch(notifyError);
  }, [jobId, applyAux]);

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
  // Só o estado da análise e as deteções novas a cada 2 s; tracks, segmentos e pessoas (recalculados no servidor)
  // no máximo de 10 em 10 s. No fim, uma recarga completa (o agrupamento final altera deteções antigas).
  usePolling(async () => {
    const j = await api.job(jobId);
    setJob(j);
    if (j.status !== "running" && j.status !== "queued") {
      loadResults();
      return;
    }
    const fresh = await api.detections(jobId, lastDetId.current);
    if (fresh.length) {
      lastDetId.current = fresh.reduce((m, x) => Math.max(m, x.id), lastDetId.current);
      const byTime = (a: Detection, b: Detection) => a.video_id - b.video_id || a.timestamp_s - b.timestamp_s;
      const faces = fresh.filter((x) => x.kind !== "body");
      const bodies = fresh.filter((x) => x.kind === "body");
      if (faces.length) setDetections((cur) => [...cur, ...faces].sort(byTime));
      if (bodies.length) setBodyDets((cur) => [...cur, ...bodies].sort(byTime));
    }
    if (fresh.length && Date.now() - lastAux.current > 10_000) {
      lastAux.current = Date.now();
      applyAux(await Promise.all([api.segments(jobId), api.tracks(jobId), api.persons(jobId)]));
    }
  }, 2000, running);

  const video = videos.find((v) => v.id === videoId) ?? null;
  const converting = video?.playback_status === "pending" || video?.playback_status === "running";
  // Atualiza o estado da cópia de reprodução. Depende só do caso e dos vídeos (valores estáveis): o objeto `job`
  // é substituído a cada 2 s enquanto a análise decorre, o que reiniciava o intervalo antes de ele disparar.
  const caseId = job?.case_id;
  const jobVideoIds = job?.video_ids.join(",") ?? "";
  usePolling(async () => {
    if (caseId == null) return;
    const ids = new Set(jobVideoIds.split(",").map(Number));
    const vs = await api.videos(caseId);
    const byId = new Map(vs.filter((v) => ids.has(v.id)).map((v) => [v.id, v]));
    setVideos((cur) => cur.map((v) => byId.get(v.id) ?? v)); // mantém a ordem por local
  }, 3000, converting && caseId != null);
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
  const viewSetFor = useRef<number | null>(null);
  useEffect(() => { // vista inicial: pessoas na descoberta, aparições no modo procurar
    if (job && viewSetFor.current !== job.id) {
      viewSetFor.current = job.id;
      setView(job.params.mode === "discover" ? "persons" : "tracks");
    }
  }, [job]);
  const gridRef = useRef<HTMLDivElement>(null);
  const trackById = useMemo(() => new Map(tracks.map((t) => [t.id, t])), [tracks]);
  // Modo procurar com várias pessoas: n.º e nome de cada uma (job.params.persons).
  const searched = !discover && (job?.params.persons?.length ?? 0) > 1 ? job!.params.persons! : [];
  const labelOf = (pid: number | null | undefined) => searched.find((p) => p.id === pid)?.label;
  const personOf = (d: Detection) => d.person_id
    ?? (d.track_id != null ? trackById.get(d.track_id)?.person_id ?? null : null);
  const inPerson = (d: Detection) => personFilter == null || personOf(d) === personFilter;
  // Uma deteção conta como correspondência se estiver abaixo do limiar ou pertencer a um track com correspondência.
  const [showRejected, setShowRejected] = useState(() => {
    try {
      return localStorage.getItem("faceai.showRejected") === "1";
    } catch {
      return false;
    }
  });
  const toggleRejected = (on: boolean) => {
    setShowRejected(on);
    try {
      localStorage.setItem("faceai.showRejected", on ? "1" : "0");
    } catch { /* sem armazenamento */ }
  };
  // Rejeitada na revisão: a própria deteção ou o track inteiro. Fica na BD e na auditoria, só deixa de se ver.
  const isRejected = (d: Detection) => d.review_status === "rejected"
    || (d.track_id != null && trackById.get(d.track_id)?.review_status === "rejected");
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
    }
  };
  // Grelhas grandes (milhares de cartões, cada um com imagens): mostram-se por páginas.
  const [limit, setLimit] = useState(PAGE);
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  // "Por rever" inclui as pessoas com revisão mista; "Confirmadas" só o que foi confirmado.
  const inReview = (status: string) => reviewFilter === "all"
    || (reviewFilter === "confirmed" ? status === "confirmed" : status !== "confirmed" && status !== "rejected");
  useEffect(() => setLimit(PAGE),
    [view, videoId, trackFilter, personFilter, minSim, showCandidates, order, evidenceFilter, locFilter, showRejected,
      reviewFilter]);
  const visible = useMemo(() => {
    const list = videoDets.filter((d) => (trackFilter == null || d.track_id === trackFilter)
      && (showRejected || !isRejected(d)) && inReview(d.review_status) && (discover
      ? inPerson(d)
      : (showCandidates || trackFilter != null || isEffectiveMatch(d)) && d.similarity >= minSim && inPerson(d)));
    if (order === "score") return [...list].sort((a, b) => b.similarity - a.similarity);
    if (order === "quality") return [...list].sort((a, b) => b.quality - a.quality);
    return list;
  }, [videoDets, showCandidates, minSim, order, trackFilter, trackById, discover, personFilter, showRejected, reviewFilter]);
  const visibleTracks = useMemo(() => {
    const list = tracks.filter((t) => t.video_id === videoId && (showRejected || t.review_status !== "rejected")
      && inReview(t.review_status) && (discover
      ? personFilter == null || t.person_id === personFilter
      : (showCandidates || t.is_match) && 1 - t.track_distance >= minSim
        && (personFilter == null || t.person_id === personFilter)));
    if (order === "score") return [...list].sort((a, b) => (discover ? b.n_faces - a.n_faces : a.track_distance - b.track_distance));
    if (order === "quality") return [...list].sort((a, b) => b.best_quality - a.best_quality);
    return list;
  }, [tracks, videoId, showCandidates, minSim, order, discover, personFilter, showRejected, reviewFilter]);
  const visiblePersons = useMemo(() => {
    const shown = persons.filter((p) => (showRejected || p.review_status !== "rejected") && inReview(p.review_status));
    if (order === "time") {
      return [...shown].sort((a, b) => a.first.video_id - b.first.video_id || a.first.start_s - b.first.start_s);
    }
    if (order === "quality") return [...shown].sort((a, b) => b.best_quality - a.best_quality);
    return shown; // por omissão: a pessoa 1 é a que mais aparece
  }, [persons, order, showRejected, reviewFilter]);
  const hasTracks = tracks.length > 0;
  const showPersons = discover && view === "persons" && trackFilter == null;
  const showTracks = hasTracks && view === "tracks" && trackFilter == null;
  const clothing = !!job?.params.clothing;
  const videoSegments = segments.filter((s) => s.video_id === videoId
    && (personFilter == null || s.person_id === personFilter)
    && (evidenceFilter === "all" || (s.evidence ?? "face").includes(evidenceFilter)));
  const sortedBodyTracks = useMemo(() => bodyTracks.filter((t) => (showRejected || t.review_status !== "rejected") && inReview(t.review_status)).sort((a, b) =>
    Number(a.evidence !== "vestuário") - Number(b.evidence !== "vestuário") || a.video_id - b.video_id
    || a.start_s - b.start_s), [bodyTracks, showRejected, reviewFilter]);
  const seekBody = (t: Track) => {
    if (t.video_id !== videoId) setVideoId(t.video_id);
    setTimeout(() => seek(t.start_s), 0);
  };
  const videoById = useMemo(() => new Map(videos.map((v) => [v.id, v])), [videos]);
  // Rejeitadas ocultas na vista atual (para o interruptor "Mostrar rejeitadas").
  const rejectedHere = view === "clothing" ? bodyTracks.filter((t) => t.review_status === "rejected").length
    : showPersons ? persons.filter((p) => p.review_status === "rejected").length
    : showTracks ? tracks.filter((t) => t.video_id === videoId && t.review_status === "rejected").length
    : videoDets.filter((d) => (trackFilter == null || d.track_id === trackFilter) && isRejected(d)).length;

  // Guarda o frame do vídeo original no instante atual do leitor (vai para os Resultados do caso).
  const saveFrame = async () => {
    const el = videoRef.current;
    if (!job || !video || !el) return;
    setSavingFrame(true);
    try {
      const sf = await api.saveFrame(job.id, video.id, el.currentTime);
      notifyOk(`Frame ${sf.frame_index} (${formatTime(sf.timestamp_s)}) guardado no separador Resultados do processo.`);
    } catch (e) {
      notifyError(e, "Não foi possível guardar o frame");
    } finally {
      setSavingFrame(false);
    }
  };
  const seek = (t: number) => {
    const el = videoRef.current;
    if (el) {
      el.currentTime = Math.max(0, t);
      el.pause();
    }
  };

  const review = async (det: Detection, status: ReviewStatus) => {
    try {
      const updated = await api.review(det.id, status);
      setDetections((ds) => ds.map((d) => (d.id === updated.id ? updated : d)));
      api.segments(jobId).then(setSegments);
      if (status === "rejected" && !showRejected) {
        notifyUndo(`${discover ? "Face" : "Deteção"} rejeitada.`, () => void review(updated, det.review_status));
      }
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
      if (status === "rejected" && !showRejected) {
        notifyUndo(`Pessoa #${p.person_id} rejeitada.`, () => void reviewPerson(p, "pending"));
      }
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
      notifyOk(`Aparição #${t.id} passou para a pessoa #${updated.person_id}.`);
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
      if (status === "rejected" && !showRejected) {
        notifyUndo(`Aparição #${track.id} rejeitada.`, () => void reviewTrack(updated, track.review_status));
      }
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
  // Aparições para a faixa de estado: tracks (com correspondência no modo procurar) e deteções soltas.
  const units = discover ? tracks.map((t) => t.review_status)
    : [...tracks.filter((t) => t.is_match).map((t) => t.review_status),
      ...detections.filter((d) => d.track_id == null && d.is_match).map((d) => d.review_status)];
  const reviewCounts = {
    total: units.length,
    pending: units.filter((x) => x !== "confirmed" && x !== "rejected").length,
    confirmed: units.filter((x) => x === "confirmed").length,
    rejected: units.filter((x) => x === "rejected").length,
  };
  const details = `${job.params.model} + ${job.params.detector} · limiar ${job.params.threshold} · ${job.params.sample_fps} fps`
    + `${job.params.preset ? ` · rigor ${({ fast: "rápida", balanced: "equilibrada", thorough: "minuciosa", custom: "personalizada" })[job.params.preset]}` : ""}`
    + ` · ${String(job.environment.engine ?? "")} ${String(job.environment.device ?? "?")} · ${job.frames_sampled} frames amostrados`
    + ` (${job.frames_skipped_motion} sem movimento, ${job.frames_skipped_noface} sem faces) · ${job.frames_processed} com deteção completa`
    + ` · ${job.faces_detected} faces · ${discover ? `${persons.length} pessoas · ${tracks.length} tracks · ${detections.length} faces guardadas`
      : `${counts.matches} deteções com correspondência (${counts.confirmed} confirmadas, ${counts.rejected} rejeitadas)`
        + `${job.params.tracking ? ` · ${counts.tracks} tracks com correspondência` : ""}`}`
    + `${clothing && !running ? ` · vestuário: ${job.clothing_samples} amostra(s), ${bodyTracks.filter((t) => t.evidence === "vestuário").length} só pela roupa` : ""}`
    + `${job.status === "completed" && job.message ? `. ${job.message}` : ""}`;
  const reviewPending = () => {
    setReviewFilter("pending");
    setTrackFilter(null);
    if (view === "clothing" || view === "detections") setView(discover ? "persons" : "tracks");
    setTimeout(() => gridRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  return (
    <Stack maw={1400} mx="auto">
      <Breadcrumbs>
        <Anchor component={Link} to="/">Processos</Anchor>
        <Anchor component={Link} to={`/cases/${job.case_id}`}>Processo #{job.case_id}</Anchor>
        <Text>Análise #{job.id}</Text>
      </Breadcrumbs>

      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap="sm"><Title order={2}>Análise #{job.id}</Title><JobStatusBadge status={job.status} /></Group>
        </div>
        <Group>
          {running && (
            <Button color="orange" variant="light" leftSection={<IconPlayerStop size={18} />}
              onClick={() => api.cancelJob(job.id).then(setJob).catch(notifyError)}>Cancelar</Button>
          )}
          {(job.status === "failed" || job.status === "cancelled") && (
            <Button variant="light" leftSection={<IconRefresh size={18} />} onClick={() => {
              if (!window.confirm("Repetir a análise com os mesmos parâmetros e vídeos? Os resultados atuais e as revisões desta análise são substituídos.")) return;
              api.retryJob(job.id).then((j) => { setJob(j); loadResults(); }).catch(notifyError);
            }}>Repetir análise</Button>
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

      <ResultsSummary job={job} discover={discover} running={running} counts={reviewCounts} persons={persons.length}
        details={details} onReviewPending={reviewPending} />
      {clothing && !running && job.clothing_summary?.note && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />}>{job.clothing_summary.note}</Alert>
      )}
      {job.partial && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />} title="Resultados incompletos">
          Esta análise foi interrompida a meio (servidor desligado ou reiniciado) e só cobre {Math.round(job.progress * 100)}%
          {" "}dos frames. {running ? "Vai recomeçar do início; os resultados atuais são substituídos." : "Use \"Repetir análise\" para a correr de novo."}
        </Alert>
      )}
      {!running && (
        <AlertsCard jobId={job.id} caseId={job.case_id} reloadKey={job.status} onChange={loadResults}
          onShowPerson={discover ? (pid) => {
            const p = persons.find((x) => x.person_id === pid);
            if (p && personFilter !== pid) selectPerson(p);
          } : undefined} />
      )}
      {job.message && job.status !== "completed" && (
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
                        startAt.current = { video: null, t: 0 };
                      }
                    }} />
                )}
                {!converting && (playError?.videoId === video.id || video.playback_status === "failed"
                  || video.playback_status === "unavailable") ? (
                  <Alert color="orange" icon={<IconAlertTriangle size={18} />}>
                    {video.playback_status === "failed" ? "Não foi possível preparar este vídeo para o browser. "
                      : video.playback_status === "unavailable" ? `${video.playback_error} `
                        : "O browser não reproduz este vídeo diretamente; a preparar uma cópia só para visualização… "}
                    Entretanto pode ver os frames de cada aparição (botão 🔍).
                    <Anchor component="button" size="xs" ml={6} onClick={() => setPlayDetails((x) => !x)}>
                      {playDetails ? "Ocultar detalhes" : "Detalhes"}</Anchor>
                    {playDetails && video.playback_status === "failed" && (
                      <Text size="xs" c="dimmed" mt="xs">{video.playback_error}</Text>
                    )}
                    {playDetails && video.playback_status !== "unavailable" && (
                      <Group gap="xs" mt="xs">
                        <Button size="compact-xs" variant="light" onClick={() => createPlaybackCopy("mp4")}>
                          Criar cópias H.264 (MP4) de todos os vídeos
                        </Button>
                        <Button size="compact-xs" variant="light" onClick={() => createPlaybackCopy("webm")}>
                          Criar cópias VP9 (WebM) de todos os vídeos
                        </Button>
                      </Group>
                    )}
                    {playDetails && playError?.videoId === video.id && (
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
                <LiveTimeline videoRef={videoRef} videoKey={`${video.id}-${video.playback_status}-${video.playback_format}-${converting}`}
                  duration={video.duration_s} segments={videoSegments}
                  detections={videoDets.filter((d) => !isRejected(d) && (discover ? inPerson(d) : isEffectiveMatch(d)))}
                  realTime={video.dvr_start ? (sec) => realTime(video, sec) : undefined} onSeek={seek} />
                {!converting && (
                  <Group justify="space-between" wrap="wrap" gap="xs">
                    <Tooltip label="Guarda o frame original (sem marcações) neste instante; fica no separador Resultados do processo como imagem validada">
                      <Button size="xs" variant="light" leftSection={<IconDeviceFloppy size={14} />} loading={savingFrame}
                        onClick={saveFrame}>Guardar frame</Button>
                    </Tooltip>
                    <Text size="xs" c="dimmed">{VIDEO_KEYS_HINT}</Text>
                  </Group>
                )}
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
                      <Table.Td><Image loading="lazy" decoding="async" src={api.detectionImageUrl(s.best_detection_id, "crop")} w={48} h={48} radius="sm" /></Table.Td>
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

      <Card withBorder ref={gridRef} style={{ scrollMarginTop: 16 }}>
        <Group justify="space-between" mb="sm" wrap="wrap">
          <Group gap="sm">
            {(hasTracks || clothing) && (
              <SegmentedControl size="xs" value={view}
                onChange={(v) => { setView(v as "persons" | "tracks" | "detections" | "clothing"); setTrackFilter(null); }}
                data={[...(discover ? [{ value: "persons", label: `Pessoas (${visiblePersons.length})` }] : []),
                  { value: "tracks", label: `Aparições (${visibleTracks.length})` },
                  { value: "detections", label: "Faces individuais" },
                  ...(clothing ? [{ value: "clothing", label: `Vestuário (${sortedBodyTracks.filter((t) => t.evidence === "vestuário").length})` }] : [])]} />
            )}
            {!showTracks && !showPersons && view !== "clothing" && <Text fw={600}>Faces individuais ({visible.length})</Text>}
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
                Aparição #{trackFilter} · mostrar tudo
              </Button>
            )}
          </Group>
          <ResultsFilters discover={discover} clothingView={view === "clothing"} review={reviewFilter}
            onReview={setReviewFilter} minSim={minSim} onMinSim={setMinSim} showCandidates={showCandidates}
            onShowCandidates={setShowCandidates} showRejected={showRejected} onShowRejected={toggleRejected}
            rejectedCount={rejectedHere} order={order} onOrder={setOrder} />
        </Group>
        {view === "clothing" && clothing ? (
          <Stack gap="xs">
            <Alert color="grape" variant="light" icon={<IconInfoCircle size={18} />}>
              <b>Evidência complementar.</b> As amostras são o corpo da pessoa nos frames em que a face teve
              correspondência (tronco {job.clothing_summary?.gallery_colors?.tronco ?? "?"}, pernas{" "}
              {job.clothing_summary?.gallery_colors?.pernas ?? "?"}). As aparições "só vestuário" foram encontradas pela
              roupa noutros vídeos/momentos e têm de ser revistas: roupa semelhante (fardas, roupa escura) pode ser de
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
            <><SimpleGrid cols={{ base: 1, xs: 2, sm: 3, md: 4, xl: 5 }}>
              {visiblePersons.slice(0, limit).map((p) => (
                <PersonCard key={p.person_id} person={p} selected={personFilter === p.person_id}
                  others={persons.filter((o) => o.person_id !== p.person_id)}
                  firstRealTime={realTime(videoById.get(p.first.video_id), p.first.start_s)}
                  onSelect={selectPerson} onReview={reviewPerson} onMerge={mergePerson}
                  onUseAsReference={(x) => addAsReference(x.best_detection_id)}
                  onFindSimilar={job.status === "completed"
                    ? (x) => setSimilar({ kind: "person", jobId: job.id, personId: x.person_id }) : undefined} />
              ))}
            </SimpleGrid><ShowMore shown={limit} total={visiblePersons.length} onMore={() => setLimit((l) => l + PAGE)} /></>
          )
        ) : showTracks ? (
          visibleTracks.length === 0 ? (
            <Text c="dimmed">Sem aparições com os filtros atuais.</Text>
          ) : (
            <><SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
              {visibleTracks.slice(0, limit).map((t) => (
                <TrackCard key={t.id} track={t} onSeek={seek} onReview={reviewTrack}
                  realStart={realTime(video, t.start_s)} realEnd={realTime(video, t.end_s)} onOpenFrame={openFrame}
                  personIds={discover ? personIds : undefined} onMove={discover ? moveTrack : undefined}
                  personLabel={labelOf(t.person_id)} personAssign={assignFor(t.best_detection_id)}
                  onFindSimilar={job.status === "completed" ? (x) => setSimilar({ kind: "track", trackId: x.id }) : undefined}
                  onShowDetections={(x) => { setTrackFilter(x.id); setView("detections"); }} />
              ))}
            </SimpleGrid><ShowMore shown={limit} total={visibleTracks.length} onMore={() => setLimit((l) => l + PAGE)} /></>
          )
        ) : visible.length === 0 ? (
          <Text c="dimmed">Sem deteções com os filtros atuais.</Text>
        ) : (
          <><SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }}>
            {visible.slice(0, limit).map((d) => (
              <DetectionCard key={d.id} det={d} onSeek={(x) => seek(x.timestamp_s)} onReview={review} onOpenFrame={setFrameDet}
                realTime={realTime(video, d.timestamp_s)} personId={personOf(d)} personLabel={labelOf(personOf(d))}
                personAssign={assignFor(d.id)} />
            ))}
          </SimpleGrid><ShowMore shown={limit} total={visible.length} onMore={() => setLimit((l) => l + PAGE)} /></>
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

/** Linha temporal com o instante atual do vídeo. O instante muda ~4×/s durante a reprodução: fica neste componente
 * (que escuta o próprio elemento de vídeo) para não re-renderizar a página inteira e as grelhas de resultados. */
function LiveTimeline({ videoRef, videoKey, ...props }: Omit<ComponentProps<typeof Timeline>, "currentTime"> & {
  videoRef: RefObject<HTMLVideoElement | null>; videoKey: string;
}) {
  const [t, setT] = useState(0);
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const update = () => setT(el.currentTime);
    update();
    el.addEventListener("timeupdate", update);
    el.addEventListener("seeked", update);
    return () => {
      el.removeEventListener("timeupdate", update);
      el.removeEventListener("seeked", update);
    };
  }, [videoRef, videoKey]);
  return <Timeline {...props} currentTime={t} />;
}

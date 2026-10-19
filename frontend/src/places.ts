import type { Job, Location, Video } from "./types";

/** Nome da câmara (por omissão, o nome do ficheiro). */
export function cameraName(v: Pick<Video, "camera_name" | "filename">): string {
  return v.camera_name?.trim() || v.filename;
}

/** Rótulo "Local · Câmara" de um vídeo. */
export function cameraLabel(v: Pick<Video, "camera_name" | "filename" | "location_id"> | null | undefined,
  locations: Pick<Location, "id" | "name">[]): string {
  if (!v) return "";
  const loc = locations.find((l) => l.id === v.location_id);
  return loc ? `${loc.name} · ${cameraName(v)}` : cameraName(v);
}

/** Vídeos agrupados por local (pela ordem dos locais; vídeos sem local no fim). */
export function groupByLocation<V extends Pick<Video, "location_id">>(videos: V[], locations: Pick<Location, "id" | "name">[]):
  { location: Pick<Location, "id" | "name"> | null; videos: V[] }[] {
  const groups: { location: Pick<Location, "id" | "name"> | null; videos: V[] }[] = locations
    .map((l) => ({ location: l, videos: videos.filter((v) => v.location_id === l.id) }));
  const orphans = videos.filter((v) => !locations.some((l) => l.id === v.location_id));
  if (orphans.length) groups.push({ location: null, videos: orphans });
  return groups;
}

/** Locais de uma análise (nome atual; análises antigas sem registo de locais → vazio). */
export function jobLocations(j: Job, locations: Pick<Location, "id" | "name">[]): string {
  return (j.params.locations ?? [])
    .map((l) => locations.find((x) => x.id === l.id)?.name ?? l.name)
    .filter(Boolean).join(", ");
}

export interface LocationAnalysis {
  state: "done" | "partial" | "none";
  analysed: number; // vídeos do local já incluídos numa análise concluída
  total: number;
  jobs: Job[]; // análises concluídas com vídeos do local (mais recente primeiro)
}

/** Estado de análise de um local: todos / alguns / nenhum dos seus vídeos numa análise concluída. */
export function locationAnalysis(locationId: number, videos: Pick<Video, "id" | "location_id">[],
  jobs: Job[]): LocationAnalysis {
  const ids = videos.filter((v) => v.location_id === locationId).map((v) => v.id);
  const done = jobs.filter((j) => j.status === "completed" && j.video_ids.some((id) => ids.includes(id)))
    .sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at));
  const covered = new Set(done.flatMap((j) => j.video_ids));
  const analysed = ids.filter((id) => covered.has(id)).length;
  const state = !ids.length || !analysed ? "none" : analysed === ids.length ? "done" : "partial";
  return { state, analysed, total: ids.length, jobs: done };
}

/** Análises concluídas que incluíram este vídeo (mais recente primeiro). */
export function videoAnalyses(videoId: number, jobs: Job[]): Job[] {
  return jobs.filter((j) => j.status === "completed" && j.video_ids.includes(videoId))
    .sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at));
}

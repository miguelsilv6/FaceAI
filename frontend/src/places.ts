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

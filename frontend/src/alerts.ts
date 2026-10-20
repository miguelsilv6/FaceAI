import type { PersonAlert } from "./types";

/** Título e frase de um alerta, conforme o tipo. */
export function alertText(a: PersonAlert): { title: string; text: string } {
  const who = a.discovered_person_id != null ? `Pessoa #${a.discovered_person_id}` : "Esta pessoa";
  if (a.kind === "places") {
    return { title: who, text: `aparece em ${a.location_ids.length} locais (sem fotos no processo)` };
  }
  if (a.kind === "linked") {
    const other = a.linked_person_id != null && a.linked_mode === "discover"
      ? `a Pessoa #${a.linked_person_id}` : `a aparição #${a.linked_track_id}`;
    return { title: who, text: `pode ser ${other} da análise #${a.linked_job_id}` };
  }
  return { title: a.case_person_name ?? "Pessoa do processo",
    text: a.discovered_person_id != null ? `pode ser a Pessoa #${a.discovered_person_id}` : "correspondências" };
}

/** Endereço da aparição ligada noutra análise (vídeo e instante). */
export function linkedUrl(a: PersonAlert): string | null {
  if (a.linked_job_id == null) return null;
  const t = a.linked_track;
  return `/jobs/${a.linked_job_id}${t ? `?video=${t.video_id}&t=${t.start_s.toFixed(2)}` : ""}`;
}

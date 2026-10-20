import { Alert, Badge, Button, Group, Image, Stack, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconCheck, IconExternalLink, IconMapPin, IconRoute, IconUser, IconX } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { alertText, linkedUrl } from "../alerts";
import { api } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { PersonAlert } from "../types";

const STATUS: Record<PersonAlert["status"], [string, string]> = {
  new: ["por rever", "orange"],
  confirmed: ["confirmado", "green"],
  dismissed: ["descartado", "gray"],
};

const hhmm = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 19) : "");

/** Alertas da análise: pessoas do processo que podem ter aparecido (e em quantos locais). */
export default function AlertsCard({ jobId, caseId, reloadKey, onShowPerson, onChange }: {
  jobId: number; caseId: number; reloadKey: string; onShowPerson?: (discoveredPersonId: number) => void;
  onChange?: () => void;
}) {
  const navigate = useNavigate();
  const [alerts, setAlerts] = useState<PersonAlert[]>([]);
  const load = useCallback(() => {
    api.jobAlerts(jobId).then(setAlerts).catch(() => setAlerts([]));
  }, [jobId]);
  useEffect(load, [load, reloadKey]);

  const review = async (a: PersonAlert, status: PersonAlert["status"]) => {
    try {
      await api.reviewAlert(a.id, status);
      notifyOk(status === "confirmed" ? `Alerta confirmado: ${alertText(a).title}.` : "Alerta atualizado.");
      load();
      onChange?.();
    } catch (e) {
      notifyError(e);
    }
  };

  if (!alerts.length) return null;
  const pending = alerts.filter((a) => a.status === "new").length;
  return (
    <Alert color="orange" variant="light" icon={<IconAlertTriangle size={20} />}
      title={`Alertas (${alerts.length}${pending ? `, ${pending} por rever` : ""})`}>
      <Stack gap="xs">
        <Text size="xs" c="dimmed">Indícios para revisão, não identificações: pessoas do processo cujas fotos se
          aproximam de faces desta análise, pessoas vistas em vários locais e pessoas parecidas com as de outras
          análises do processo.</Text>
        {alerts.map((a) => {
          const [label, color] = STATUS[a.status];
          const { title, text } = alertText(a);
          const link = linkedUrl(a);
          return (
            <Group key={a.id} justify="space-between" wrap="wrap" gap="xs"
              style={{ borderTop: "1px solid var(--mantine-color-default-border)", paddingTop: 6 }}>
              <Group gap="xs" wrap="nowrap">
                {a.reference_id != null && (
                  <Tooltip label={`Foto de ${title}`}>
                    <Image loading="lazy" decoding="async" src={api.referenceImageUrl(a.reference_id, a.reference_has_crop)} w={44} h={44} radius="sm"
                      fit="cover" alt={title} />
                  </Tooltip>
                )}
                {a.best_detection_id != null && (
                  <Tooltip label={a.discovered_person_id != null ? `Pessoa #${a.discovered_person_id} (esta análise)` : "Melhor correspondência"}>
                    <Image loading="lazy" decoding="async" src={api.detectionImageUrl(a.best_detection_id, "crop")} w={44} h={44} radius="sm"
                      fit="cover" alt="Face da análise" />
                  </Tooltip>
                )}
                {a.linked_track?.best_detection_id != null && (
                  <Tooltip label={`Na análise #${a.linked_job_id}`}>
                    <Image loading="lazy" decoding="async" src={api.detectionImageUrl(a.linked_track.best_detection_id, "crop")}
                      w={44} h={44} radius="sm" fit="cover" alt="Face da outra análise" />
                  </Tooltip>
                )}
                <Stack gap={0}>
                  <Group gap={6}>
                    <Text fw={700}>{title}</Text>
                    <Text size="sm">{text}</Text>
                    {a.kind !== "places" && <Badge size="sm" variant="light">{(a.similarity * 100).toFixed(0)}%</Badge>}
                    {a.highlighted && <Badge size="sm" color="red" leftSection={<IconMapPin size={10} />}>
                      {a.location_ids.length} locais</Badge>}
                    <Badge size="sm" color={color} variant="outline">{label}</Badge>
                  </Group>
                  <Text size="xs" c="dimmed">
                    {a.location_names.join(" · ") || "—"} · {a.n_tracks} aparição(ões)
                    {a.first_real ? ` · ${hhmm(a.first_real)}${a.last_real ? ` → ${hhmm(a.last_real)}` : ""}` : ""}
                  </Text>
                </Stack>
              </Group>
              <Group gap={6}>
                {a.discovered_person_id != null && onShowPerson && (
                  <Button size="compact-xs" variant="light" leftSection={<IconUser size={12} />}
                    onClick={() => onShowPerson(a.discovered_person_id!)}>Ver pessoa</Button>
                )}
                {link && (
                  <Button size="compact-xs" variant="light" leftSection={<IconExternalLink size={12} />}
                    onClick={() => navigate(link)}>Abrir análise #{a.linked_job_id}</Button>
                )}
                <Button size="compact-xs" variant="light" leftSection={<IconRoute size={12} />}
                  onClick={() => navigate(`/cases/${caseId}?tab=route`)}>Ver percurso</Button>
                {a.status !== "confirmed" && (
                  <Button size="compact-xs" color="green" variant="light" leftSection={<IconCheck size={12} />}
                    onClick={() => review(a, "confirmed")}>Confirmar</Button>
                )}
                {a.status !== "dismissed" && (
                  <Button size="compact-xs" color="gray" variant="light" leftSection={<IconX size={12} />}
                    onClick={() => review(a, "dismissed")}>Descartar</Button>
                )}
                {a.status !== "new" && (
                  <Button size="compact-xs" variant="subtle" onClick={() => review(a, "new")}>Reabrir</Button>
                )}
              </Group>
            </Group>
          );
        })}
      </Stack>
    </Alert>
  );
}

import {
  Alert, Anchor, Badge, Box, Button, Card, Checkbox, Chip, Group, Image, Loader, Select, SimpleGrid, Stack, Text,
} from "@mantine/core";
import { IconDownload, IconExternalLink, IconInfoCircle, IconPhotoDown } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatTime } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { ValidatedItem } from "../types";

/** Frames validados do caso (deteções confirmadas na revisão), para escolher e descarregar os originais. */
export default function ValidatedFramesPanel({ caseId }: { caseId: number }) {
  const navigate = useNavigate();
  const [items, setItems] = useState<ValidatedItem[] | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [loc, setLoc] = useState("all");
  const [person, setPerson] = useState<string | null>(null);
  const [job, setJob] = useState<string | null>(null);

  useEffect(() => {
    api.validatedItems(caseId).then((xs) => { setItems(xs); setSelected(xs.map((x) => x.detection_id)); })
      .catch(notifyError);
  }, [caseId]);

  const locations = useMemo(() => [...new Map((items ?? []).filter((i) => i.location_id != null)
    .map((i) => [i.location_id!, i.location_name])).entries()], [items]);
  const persons = useMemo(() => [...new Set((items ?? []).map((i) => i.person_label).filter(Boolean))].sort(), [items]);
  const jobs = useMemo(() => [...new Set((items ?? []).map((i) => i.job_id))].sort((a, b) => a - b), [items]);
  const visible = (items ?? []).filter((i) => (loc === "all" || String(i.location_id) === loc)
    && (!person || i.person_label === person) && (!job || String(i.job_id) === job));
  const chosen = visible.filter((i) => selected.includes(i.detection_id));
  const frames = new Set(chosen.map((i) => `${i.job_id}:${i.video_id}:${i.frame_index}`)).size;

  const toggle = (id: number) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const setVisible = (on: boolean) => {
    const ids = new Set(visible.map((i) => i.detection_id));
    setSelected((s) => (on ? [...new Set([...s, ...ids])] : s.filter((x) => !ids.has(x))));
  };
  const download = (ids: number[]) => {
    notifyOk(`A preparar o ZIP com ${ids.length} deteção(ões) confirmada(s)…`, "Download");
    window.location.href = api.validatedFramesUrl("cases", caseId, ids);
  };

  if (!items) return <Loader size="sm" />;
  if (!items.length) {
    return (
      <Alert color="blue" icon={<IconInfoCircle size={18} />}>
        Ainda não há frames validados neste caso. Confirme as deteções (✓) nos resultados das análises: as
        confirmadas aparecem aqui para descarregar os frames originais.
      </Alert>
    );
  }
  return (
    <Stack>
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Group align="flex-end" wrap="wrap">
          {locations.length > 1 && (
            <Chip.Group multiple={false} value={loc} onChange={(v) => setLoc(v as string)}>
              <Group gap={4}>
                <Chip size="xs" value="all">Todos os locais</Chip>
                {locations.map(([id, name]) => <Chip key={id} size="xs" value={String(id)} color="indigo">{name}</Chip>)}
              </Group>
            </Chip.Group>
          )}
          {persons.length > 1 && (
            <Select size="xs" w={200} label="Pessoa" placeholder="Todas" clearable value={person} onChange={setPerson}
              data={persons} />
          )}
          {jobs.length > 1 && (
            <Select size="xs" w={150} label="Análise" placeholder="Todas" clearable value={job} onChange={setJob}
              data={jobs.map((j) => ({ value: String(j), label: `#${j}` }))} />
          )}
        </Group>
        <Group gap="xs">
          <Button size="xs" variant="default" onClick={() => setVisible(true)}>Selecionar todos</Button>
          <Button size="xs" variant="default" onClick={() => setVisible(false)}>Limpar seleção</Button>
          <Button size="xs" leftSection={<IconDownload size={14} />} disabled={!chosen.length}
            onClick={() => download(chosen.map((i) => i.detection_id))}>
            Descarregar selecionados ({chosen.length})
          </Button>
        </Group>
      </Group>
      <Text size="sm" c="dimmed">
        {visible.length} deteção(ões) confirmada(s){visible.length !== items.length ? ` de ${items.length}` : ""} ·
        {" "}{chosen.length} selecionada(s), {frames} frame(s). O ZIP leva o frame original (PNG, sem marcações) de cada
        deteção selecionada, o manifesto com SHA-256 e o registo fica na auditoria.
      </Text>
      <SimpleGrid cols={{ base: 1, xs: 2, md: 3, xl: 4 }}>
        {visible.map((i) => {
          const on = selected.includes(i.detection_id);
          return (
            <Card key={i.detection_id} withBorder padding="xs"
              style={{ outline: on ? "2px solid var(--mantine-color-blue-6)" : undefined }}>
              <Card.Section style={{ position: "relative", cursor: "pointer" }} onClick={() => toggle(i.detection_id)}>
                <Image src={api.detectionFrameUrl(i.detection_id, "annotated")} h={170} fit="contain" bg="black"
                  alt={`Frame da deteção ${i.detection_id}`} />
                <Box style={{ position: "absolute", top: 8, left: 8 }}>
                  <Checkbox checked={on} onChange={() => toggle(i.detection_id)} onClick={(e) => e.stopPropagation()}
                    aria-label={`Selecionar deteção ${i.detection_id}`} />
                </Box>
              </Card.Section>
              <Stack gap={2} mt={6}>
                <Group justify="space-between" wrap="nowrap" gap={4}>
                  <Text size="sm" fw={600} truncate title={i.video_filename}>{i.camera_label || i.video_filename}</Text>
                  {i.person_label && <Badge size="sm" variant="light">{i.person_label}</Badge>}
                </Group>
                <Text size="xs" ff="monospace">
                  {i.real_time ? i.real_time.replace("T", " ").slice(0, 19) : "sem hora real"}
                  {" "}· vídeo {formatTime(i.timestamp_s)}
                </Text>
                <Text size="xs" c="dimmed">
                  Análise #{i.job_id} · deteção #{i.detection_id} · frame {i.frame_index}
                  {i.kind === "body" ? " · vestuário" : ` · ${(i.similarity * 100).toFixed(0)}%`}
                </Text>
                <Text size="xs" c="dimmed" truncate>
                  Confirmada por {i.reviewed_by || "—"}{i.review_note ? ` · ${i.review_note}` : ""}
                </Text>
                <Group gap="xs" mt={4}>
                  <Anchor size="xs" href={api.detectionFrameUrl(i.detection_id, "original", true)}>
                    <Group gap={2} wrap="nowrap"><IconPhotoDown size={12} />PNG original</Group>
                  </Anchor>
                  <Anchor size="xs" component="button"
                    onClick={() => navigate(`/jobs/${i.job_id}?video=${i.video_id}&t=${i.timestamp_s}`)}>
                    <Group gap={2} wrap="nowrap"><IconExternalLink size={12} />Abrir na análise</Group>
                  </Anchor>
                </Group>
              </Stack>
            </Card>
          );
        })}
      </SimpleGrid>
    </Stack>
  );
}

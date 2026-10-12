import {
  Accordion, Button, Card, Group, MultiSelect, NumberInput, Progress, Select, SimpleGrid, Stack, Table, Text, Title,
} from "@mantine/core";
import { IconPlayerPlay } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatDate } from "../api";
import { notifyError } from "../notify";
import type { Job, JobParams, ReferenceImage, SystemInfo, Video } from "../types";
import JobStatusBadge from "./JobStatusBadge";

interface Props {
  caseId: number;
  jobs: Job[];
  videos: Video[];
  references: ReferenceImage[];
  onChange: () => void;
}

export default function JobsPanel({ caseId, jobs, videos, references, onChange }: Props) {
  const navigate = useNavigate();
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [params, setParams] = useState<JobParams | null>(null);
  const [videoIds, setVideoIds] = useState<string[]>([]);
  const [refIds, setRefIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const validRefs = useMemo(() => references.filter((r) => r.valid), [references]);

  useEffect(() => {
    api.system().then((s) => {
      setSystem(s);
      const threshold = s.models.find((m) => m.name === s.defaults.model)?.default_threshold ?? null;
      setParams({ ...s.defaults, threshold });
    }).catch(notifyError);
  }, []);
  useEffect(() => setVideoIds(videos.map((v) => String(v.id))), [videos]);
  useEffect(() => setRefIds(validRefs.map((r) => String(r.id))), [validRefs]);

  // Atualiza a lista enquanto houver análises a decorrer.
  const active = jobs.some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(onChange, 1500);
    return () => clearInterval(t);
  }, [active, onChange]);

  const set = <K extends keyof JobParams>(key: K, value: JobParams[K]) => setParams((p) => (p ? { ...p, [key]: value } : p));

  const start = async () => {
    if (!params) return;
    setSubmitting(true);
    try {
      const job = await api.createJob(caseId, params, videoIds.map(Number), refIds.map(Number));
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      notifyError(e, "Não foi possível iniciar a análise");
    } finally {
      setSubmitting(false);
    }
  };

  const num = (v: string | number, fallback: number) => (typeof v === "number" ? v : Number(v) || fallback);

  return (
    <Stack>
      <Card withBorder>
        <Title order={4} mb="sm">Nova análise</Title>
        {!params || !system ? (
          <Text c="dimmed">A carregar configuração…</Text>
        ) : (
          <Stack>
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <MultiSelect label="Vídeos" data={videos.map((v) => ({ value: String(v.id), label: `#${v.id} ${v.filename}` }))}
                value={videoIds} onChange={setVideoIds} placeholder="Selecione os vídeos" />
              <MultiSelect label="Imagens de referência (válidas)"
                data={validRefs.map((r) => ({ value: String(r.id), label: `#${r.id} ${r.filename}` }))}
                value={refIds} onChange={setRefIds} placeholder="Selecione as referências" />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
              <Select label="Modelo de reconhecimento" data={system.models.map((m) => m.name)} value={params.model}
                allowDeselect={false}
                onChange={(m) => m && setParams({ ...params, model: m,
                  threshold: system.models.find((x) => x.name === m)?.default_threshold ?? params.threshold })} />
              <Select label="Detetor facial" data={system.detectors} value={params.detector} allowDeselect={false}
                onChange={(d) => d && set("detector", d)} />
              <NumberInput label="Frames analisados por segundo" min={0.1} max={60} step={0.5} decimalScale={2}
                value={params.sample_fps} onChange={(v) => set("sample_fps", num(v, 2))} />
              <NumberInput label="Limiar (distância de cosseno)" min={0.01} max={1.99} step={0.01} decimalScale={3}
                description="Menor = mais restritivo" value={params.threshold ?? ""}
                onChange={(v) => set("threshold", num(v, 0.68))} />
            </SimpleGrid>
            <Accordion variant="contained">
              <Accordion.Item value="adv">
                <Accordion.Control>Parâmetros avançados</Accordion.Control>
                <Accordion.Panel>
                  <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
                    <NumberInput label="Margem de candidatos" description="Guardar para revisão até limiar + margem"
                      min={0} max={0.5} step={0.01} decimalScale={3} value={params.candidate_margin}
                      onChange={(v) => set("candidate_margin", num(v, 0.08))} />
                    <NumberInput label="Tamanho mínimo da face (px)" min={0} value={params.min_face_size}
                      onChange={(v) => set("min_face_size", num(v, 20))} />
                    <NumberInput label="Confiança mínima do detetor" min={0} max={1} step={0.05} decimalScale={2}
                      value={params.min_confidence} onChange={(v) => set("min_confidence", num(v, 0.5))} />
                    <NumberInput label="Ampliação dos frames" description="Ajuda com faces pequenas (mais lento)"
                      min={1} max={4} step={0.5} decimalScale={1} value={params.upscale}
                      onChange={(v) => set("upscale", num(v, 1))} />
                    <NumberInput label="Intervalo máx. no segmento (s)" min={0} step={0.5} decimalScale={1}
                      value={params.segment_gap_s} onChange={(v) => set("segment_gap_s", num(v, 2))} />
                  </SimpleGrid>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
            <Group justify="flex-end">
              <Button leftSection={<IconPlayerPlay size={18} />} onClick={start} loading={submitting}
                disabled={!videoIds.length || !refIds.length}>
                Iniciar análise
              </Button>
            </Group>
          </Stack>
        )}
      </Card>

      <Card withBorder p={0}>
        {jobs.length === 0 ? (
          <Text c="dimmed" p="md">Ainda não foram feitas análises neste caso.</Text>
        ) : (
          <Table.ScrollContainer minWidth={760}>
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr><Table.Th>#</Table.Th><Table.Th>Estado</Table.Th><Table.Th>Progresso</Table.Th>
                  <Table.Th>Modelo / detetor</Table.Th><Table.Th>Correspondências</Table.Th><Table.Th>Criada</Table.Th>
                  <Table.Th>Analista</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {jobs.map((j) => (
                  <Table.Tr key={j.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/jobs/${j.id}`)}>
                    <Table.Td>{j.id}</Table.Td>
                    <Table.Td><JobStatusBadge status={j.status} /></Table.Td>
                    <Table.Td w={160}><Progress value={j.progress * 100} animated={j.status === "running"} /></Table.Td>
                    <Table.Td>{j.params.model} / {j.params.detector}</Table.Td>
                    <Table.Td>{j.matches}</Table.Td>
                    <Table.Td>{formatDate(j.created_at)}</Table.Td>
                    <Table.Td>{j.created_by}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}

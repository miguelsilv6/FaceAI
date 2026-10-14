import {
  Alert, Anchor, Badge, Card, Group, Image, Loader, Modal, SegmentedControl, Slider, Stack, Table, Text,
} from "@mantine/core";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, formatTime } from "../api";
import type { SimilarResult } from "../types";

export type SimilarQuery = { kind: "track"; trackId: number } | { kind: "person"; jobId: number; personId: number };

const REVIEW: Record<string, [string, string]> = {
  pending: ["Por rever", "gray"], confirmed: ["Confirmado", "green"], rejected: ["Rejeitado", "red"],
};

/** Procura a mesma pessoa noutras análises (casos a que o utilizador tem acesso), pelo embedding do track. */
export default function SimilarModal({ query, defaultThreshold, onClose }: {
  query: SimilarQuery | null; defaultThreshold: number; onClose: () => void;
}) {
  const [scope, setScope] = useState<"all" | "case">("all");
  const [threshold, setThreshold] = useState(defaultThreshold);
  const [data, setData] = useState<SimilarResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => setThreshold(defaultThreshold), [defaultThreshold, query]);
  useEffect(() => {
    if (!query) return;
    let alive = true;
    setLoading(true);
    setError(null);
    const t = setTimeout(() => {
      const req = query.kind === "track" ? api.similarToTrack(query.trackId, scope, threshold)
        : api.similarToPerson(query.jobId, query.personId, scope, threshold);
      req.then((d) => alive && setData(d)).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
        .finally(() => alive && setLoading(false));
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [query, scope, threshold]);

  const title = !query ? "" : query.kind === "track" ? `Track #${query.trackId} noutras análises`
    : `Pessoa #${query.personId} noutras análises`;
  return (
    <Modal opened={!!query} onClose={onClose} title={title} size="xl">
      <Stack>
        <Group justify="space-between" wrap="wrap">
          <SegmentedControl size="xs" value={scope} onChange={(v) => setScope(v as "all" | "case")}
            data={[{ value: "all", label: "Todos os meus casos" }, { value: "case", label: "Só este caso" }]} />
          <Stack gap={2} w={260}>
            <Text size="xs">Distância máxima: {threshold.toFixed(2)} (semelhança ≥ {((1 - threshold) * 100).toFixed(0)}%)</Text>
            <Slider min={0.05} max={0.9} step={0.01} value={threshold} onChange={setThreshold} label={null} />
          </Stack>
        </Group>
        <Text size="xs" c="dimmed">Compara o embedding do track (média das melhores faces) com os tracks de outras análises
          feitas com o mesmo modelo. São candidatos: confirme sempre visualmente. A pesquisa fica na auditoria.</Text>
        {error && <Alert color="red">{error}</Alert>}
        {loading && !data && <Loader size="sm" />}
        {data && (
          <>
            <Text size="sm">
              {data.results.length} resultado(s) em {data.jobs_searched} análise(s) · modelo {data.model}
              {data.jobs_other_model > 0 && ` · ${data.jobs_other_model} com outro modelo (não comparáveis)`}
              {data.jobs_not_indexed > 0 && ` · ${data.jobs_not_indexed} sem índice (anteriores a esta versão)`}
            </Text>
            {data.results.length > 0 && (
              <Card withBorder p={0}>
                <Table.ScrollContainer minWidth={700}>
                  <Table highlightOnHover verticalSpacing={4}>
                    <Table.Thead>
                      <Table.Tr><Table.Th>Face</Table.Th><Table.Th>Caso / análise</Table.Th><Table.Th>Vídeo</Table.Th>
                        <Table.Th>Quando</Table.Th><Table.Th>Semelhança</Table.Th><Table.Th>Revisão</Table.Th></Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {data.results.map((r) => (
                        <Table.Tr key={`${r.job_id}-${r.track_id}`}>
                          <Table.Td w={64}>
                            {r.best_detection_id != null && (
                              <Image src={api.detectionImageUrl(r.best_detection_id, "crop")} h={48} w={48} fit="cover"
                                radius="sm" alt={`Track ${r.track_id}`} />
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Text size="sm" fw={500}>#{r.case_id} {r.case_name}</Text>
                            <Anchor component={Link} to={`/jobs/${r.job_id}`} size="xs" onClick={onClose}>
                              Análise #{r.job_id} · track #{r.track_id}{r.person_id ? ` · pessoa #${r.person_id}` : ""}
                            </Anchor>
                          </Table.Td>
                          <Table.Td><Text size="xs">{r.video_filename}</Text></Table.Td>
                          <Table.Td>
                            {r.real_start && <Text size="xs" ff="monospace" fw={600}>{r.real_start}</Text>}
                            <Text size="xs" ff="monospace" c="dimmed">{formatTime(r.start_s)}–{formatTime(r.end_s)}</Text>
                          </Table.Td>
                          <Table.Td><Badge color={r.similarity >= 1 - defaultThreshold ? "red" : "orange"}>
                            {(r.similarity * 100).toFixed(1)}%</Badge></Table.Td>
                          <Table.Td><Badge variant="light" color={REVIEW[r.review_status]?.[1]}>
                            {REVIEW[r.review_status]?.[0]}</Badge></Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              </Card>
            )}
          </>
        )}
      </Stack>
    </Modal>
  );
}

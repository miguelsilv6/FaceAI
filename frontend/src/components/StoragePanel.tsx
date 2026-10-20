import { Badge, Button, Card, Group, Loader, Table, Text, Title, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";

import { api, formatBytes } from "../api";
import { confirmAction } from "../confirm";
import { notifyError, notifyOk } from "../notify";
import type { CaseStorage, StorageCategory, StorageKey } from "../types";

export const STORAGE_COLORS: Record<StorageKey, string> = {
  originals: "blue.7", analysis: "indigo.5", references: "grape.5", crops: "teal.6",
  playback: "orange.5", frames: "yellow.5", caches: "gray.5", other: "gray.3",
};

const FREE_ACTIONS: { key: StorageKey; label: string; confirm: string }[] = [
  { key: "playback", label: "Libertar cópias de reprodução",
    confirm: "As cópias de reprodução são apagadas e voltam a ser criadas quando o vídeo for aberto no leitor." },
  { key: "frames", label: "Libertar frames anotados",
    confirm: "Os frames anotados gravados por análises anteriores são apagados; passam a ser gerados a pedido a partir do vídeo." },
  { key: "caches", label: "Limpar caches",
    confirm: "As caches (frames, PNG originais e miniaturas) são apagadas e refeitas quando forem precisas." },
];

/** Barra empilhada com a proporção de cada categoria. */
export function StorageBar({ categories, total }: { categories: StorageCategory[]; total: number }) {
  return (
    <div style={{ display: "flex", height: 14, borderRadius: 4, overflow: "hidden",
      background: "var(--mantine-color-default-border)" }}>
      {total > 0 && categories.filter((c) => c.bytes > 0).map((c) => (
        <Tooltip key={c.key} label={`${c.label}: ${formatBytes(c.bytes)}`}>
          <div style={{ width: `${(c.bytes / total) * 100}%`, minWidth: 2,
            background: `var(--mantine-color-${STORAGE_COLORS[c.key].replace(".", "-")})` }} />
        </Tooltip>
      ))}
    </div>
  );
}

/** Armazenamento do processo por categoria; quem gere o processo pode libertar o que é regenerável. */
export default function StoragePanel({ caseId, canManage }: { caseId: number; canManage: boolean }) {
  const [data, setData] = useState<CaseStorage | null>(null);
  const [busy, setBusy] = useState<StorageKey | null>(null);

  const load = useCallback((fresh = false) => {
    api.caseStorage(caseId, fresh).then(setData).catch(notifyError);
  }, [caseId]);
  useEffect(() => load(), [load]);

  const free = async (key: StorageKey, confirm: string) => {
    if (!await confirmAction({ title: "Libertar espaço", confirmLabel: "Libertar",
      message: `${confirm}\n\nA evidência (vídeos, fotos de referência e recortes) não é tocada.` })) return;
    setBusy(key);
    try {
      const r = await api.freeStorage(caseId, [key]);
      const freed = Object.values(r.freed).reduce((a, f) => a + f.bytes, 0);
      const files = Object.values(r.freed).reduce((a, f) => a + f.files, 0);
      notifyOk(`Libertados ${formatBytes(freed)} (${files} ficheiro(s)).`);
      setData(r.storage);
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>Armazenamento</Title>
        <Group gap="xs">
          {data && <Text size="sm" c="dimmed">
            {formatBytes(data.total_bytes)} · {formatBytes(data.regenerable_bytes)} regenerável
          </Text>}
          <Button size="compact-xs" variant="subtle" aria-label="Atualizar armazenamento"
            onClick={() => load(true)}><IconRefresh size={14} /></Button>
        </Group>
      </Group>
      {!data ? <Loader size="sm" /> : (
        <>
          <StorageBar categories={data.categories} total={data.total_bytes} />
          <Table.ScrollContainer minWidth={520} mt="xs">
            <Table verticalSpacing={4}>
              <Table.Thead>
                <Table.Tr><Table.Th>Categoria</Table.Th><Table.Th ta="right">Tamanho</Table.Th>
                  <Table.Th ta="right">Ficheiros</Table.Th><Table.Th>Tipo</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.categories.filter((c) => c.files || c.bytes).map((c) => (
                  <Table.Tr key={c.key}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <div style={{ width: 10, height: 10, borderRadius: 2,
                          background: `var(--mantine-color-${STORAGE_COLORS[c.key].replace(".", "-")})` }} />
                        <Text size="sm">{c.label}</Text>
                      </Group>
                    </Table.Td>
                    <Table.Td ta="right"><Text size="sm" ff="monospace">{formatBytes(c.bytes)}</Text></Table.Td>
                    <Table.Td ta="right"><Text size="sm" ff="monospace">{c.files ?? "—"}</Text></Table.Td>
                    <Table.Td>{c.regenerable
                      ? <Badge size="xs" variant="light" color="orange">regenerável</Badge>
                      : <Badge size="xs" variant="light" color="blue">evidência / dados</Badge>}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {canManage && (
            <Group gap="xs" mt="xs">
              {FREE_ACTIONS.map((a) => {
                const cat = data.categories.find((c) => c.key === a.key);
                return (
                  <Button key={a.key} size="xs" variant="light" color="orange" loading={busy === a.key}
                    disabled={!cat?.files || (busy !== null && busy !== a.key)}
                    onClick={() => free(a.key, a.confirm)}>
                    {a.label}{cat?.bytes ? ` (${formatBytes(cat.bytes)})` : ""}
                  </Button>
                );
              })}
            </Group>
          )}
          <Text size="xs" c="dimmed" mt="xs">
            Só se libertam ficheiros regeneráveis a partir do vídeo; a operação fica na auditoria.
            Os frames anotados das novas análises já não são gravados: são gerados a pedido.
          </Text>
        </>
      )}
    </Card>
  );
}

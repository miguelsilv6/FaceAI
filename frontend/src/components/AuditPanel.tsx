import { Alert, Button, Card, Code, Group, Stack, Table, Text } from "@mantine/core";
import { IconShieldCheck, IconShieldX } from "@tabler/icons-react";
import { useState } from "react";

import { api, formatDate } from "../api";
import { notifyError } from "../notify";
import type { AuditChainCheck, AuditEvent } from "../types";

export default function AuditPanel({ caseId, events }: { caseId?: number; events: AuditEvent[] }) {
  const [check, setCheck] = useState<AuditChainCheck | null>(null);
  const [checking, setChecking] = useState(false);
  if (caseId === undefined) return <AuditTable events={events} />;
  const verify = async () => {
    setChecking(true);
    try {
      setCheck(await api.verifyAudit(caseId));
    } catch (e) {
      notifyError(e);
    } finally {
      setChecking(false);
    }
  };
  return (
    <Stack>
      <Group justify="space-between" wrap="wrap">
        <Text size="sm" c="dimmed">Cada evento guarda o SHA-256 do anterior e do seu conteúdo: alterar, apagar ou inserir um
          evento fora da aplicação quebra a cadeia.</Text>
        <Button size="xs" variant="light" leftSection={<IconShieldCheck size={14} />} loading={checking}
          onClick={verify}>Verificar integridade da auditoria</Button>
      </Group>
      {check && (
        <Alert color={check.ok ? "teal" : "red"} icon={check.ok ? <IconShieldCheck size={18} /> : <IconShieldX size={18} />}
          title={check.ok ? "Cadeia de auditoria íntegra" : "Cadeia de auditoria quebrada"}>
          {check.ok
            ? <>{check.events} evento(s) verificados em toda a aplicação ({check.case_events} deste processo). Último hash:{" "}
              <Code>{check.last_hash.slice(0, 16)}…</Code></>
            : <>No evento #{check.broken_at ?? "—"}: {check.reason}.</>}
          {check.unsealed > 0 && <Text size="sm">{check.unsealed} evento(s) ainda sem hash.</Text>}
        </Alert>
      )}
      <AuditTable events={events} />
    </Stack>
  );
}

function AuditTable({ events }: { events: AuditEvent[] }) {
  if (!events.length) return <Text c="dimmed">Sem eventos.</Text>;
  return (
    <Card withBorder p={0}>
      <Table.ScrollContainer minWidth={800}>
        <Table striped fz="sm">
          <Table.Thead>
            <Table.Tr><Table.Th>#</Table.Th><Table.Th>Data/hora</Table.Th><Table.Th>Utilizador</Table.Th><Table.Th>IP</Table.Th>
              <Table.Th>Ação</Table.Th><Table.Th>Detalhes</Table.Th></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {events.map((e) => (
              <Table.Tr key={e.id}>
                <Table.Td>{e.id}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{formatDate(e.timestamp)}</Table.Td>
                <Table.Td>{e.actor}</Table.Td>
                <Table.Td>{e.ip || "—"}</Table.Td>
                <Table.Td><Code>{e.action}</Code></Table.Td>
                <Table.Td><Code block style={{ maxWidth: 520, whiteSpace: "pre-wrap" }}>{JSON.stringify(e.details)}</Code></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}

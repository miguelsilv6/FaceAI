import { Card, Code, Table, Text } from "@mantine/core";

import { formatDate } from "../api";
import type { AuditEvent } from "../types";

export default function AuditPanel({ events }: { events: AuditEvent[] }) {
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

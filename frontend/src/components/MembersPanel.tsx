import { Badge, Button, Card, Group, Select, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconUserPlus } from "@tabler/icons-react";
import { useState, type FormEvent } from "react";

import { api, formatDate } from "../api";
import { useAuth } from "../auth";
import { notifyError, notifyOk } from "../notify";
import type { CaseMember } from "../types";

/** Quem tem acesso ao caso (need-to-know). O responsável (owner) ou um administrador gere a lista. */
export default function MembersPanel({ caseId, members, canManage, onChange }: {
  caseId: number; members: CaseMember[]; canManage: boolean; onChange: () => void;
}) {
  const { user } = useAuth();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<"member" | "owner">("member");

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.addMember(caseId, username.trim().toLowerCase(), role);
      notifyOk(`${username} tem agora acesso ao caso.`);
      setUsername("");
      onChange();
    } catch (err) {
      notifyError(err);
    }
  };

  const remove = async (m: CaseMember) => {
    const self = m.user_id === user.id;
    if (!window.confirm(self ? "Remover o seu próprio acesso a este caso?" : `Remover o acesso de ${m.username}?`)) return;
    try {
      await api.removeMember(caseId, m.user_id);
      onChange();
    } catch (err) {
      notifyError(err);
    }
  };

  return (
    <Stack>
      <Text size="sm" c="dimmed">Só os utilizadores desta lista (e os administradores) veem o caso. O responsável
        pode editar e eliminar o caso e gerir a equipa; os membros trabalham nas análises.</Text>
      <Card withBorder p={0}>
        <Table>
          <Table.Thead>
            <Table.Tr><Table.Th>Utilizador</Table.Th><Table.Th>Papel</Table.Th><Table.Th>Adicionado</Table.Th>
              <Table.Th /></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {members.map((m) => (
              <Table.Tr key={m.user_id}>
                <Table.Td>
                  <Text fw={500}>{m.username} {!m.active && <Badge size="xs" color="gray">inativo</Badge>}</Text>
                  <Text size="xs" c="dimmed">{m.display_name}</Text>
                </Table.Td>
                <Table.Td>{m.role === "owner" ? <Badge>responsável</Badge> : <Badge variant="light">membro</Badge>}
                </Table.Td>
                <Table.Td><Text size="xs">{formatDate(m.added_at)} · {m.added_by}</Text></Table.Td>
                <Table.Td>
                  {canManage && (
                    <Group gap={4} justify="flex-end">
                      {m.role === "member" && (
                        <Button size="compact-xs" variant="subtle" onClick={async () => {
                          try {
                            await api.addMember(caseId, m.username, "owner");
                            onChange();
                          } catch (err) {
                            notifyError(err);
                          }
                        }}>Tornar responsável</Button>
                      )}
                      <Button size="compact-xs" variant="subtle" color="red" onClick={() => remove(m)}>Remover</Button>
                    </Group>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
      {!members.length && <Text c="dimmed" size="sm">Caso sem equipa (anterior às contas): só os administradores
        o veem até lhe atribuírem um responsável.</Text>}
      {canManage && (
        <form onSubmit={add}>
          <Group align="flex-end">
            <TextInput label="Dar acesso a" placeholder="nome de utilizador" required value={username}
              onChange={(e) => setUsername(e.currentTarget.value)} />
            <Select label="Papel" w={160} allowDeselect={false} value={role}
              onChange={(v) => setRole((v as "member" | "owner") ?? "member")}
              data={[{ value: "member", label: "Membro" }, { value: "owner", label: "Responsável" }]} />
            <Button type="submit" leftSection={<IconUserPlus size={16} />}>Adicionar</Button>
          </Group>
        </form>
      )}
    </Stack>
  );
}

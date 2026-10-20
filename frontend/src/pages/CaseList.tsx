import { ActionIcon, Badge, Button, Card, Group, Modal, Stack, Table, Text, TextInput, Textarea, Title, Tooltip } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconBellRinging, IconFolderPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatBytes, formatDate } from "../api";
import DeleteCaseModal from "../components/DeleteCaseModal";
import { notifyError } from "../notify";
import type { Case } from "../types";

export default function CaseList() {
  const [cases, setCases] = useState<Case[]>([]);
  const [opened, { open, close }] = useDisclosure(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [toDelete, setToDelete] = useState<Case | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.cases().then(setCases).catch(notifyError);
  }, []);

  const create = async () => {
    try {
      const c = await api.createCase({ name, description });
      close();
      navigate(`/cases/${c.id}`);
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <Stack maw={1100} mx="auto">
      <Group justify="space-between">
        <Title order={2}>Processos</Title>
        <Button leftSection={<IconFolderPlus size={18} />} onClick={open}>Novo processo</Button>
      </Group>
      <Card withBorder p={0}>
        {cases.length === 0 ? (
          <Stack align="center" p="xl" gap="sm">
            <Text c="dimmed">Ainda não existem processos. Crie um para importar as fotos da pessoa e os vídeos.</Text>
            <Button leftSection={<IconFolderPlus size={18} />} onClick={open}>Criar processo</Button>
          </Stack>
        ) : (
          <Table.ScrollContainer minWidth={600}>
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr><Table.Th>#</Table.Th><Table.Th>Nome</Table.Th><Table.Th>Descrição</Table.Th>
                  <Table.Th>Analista</Table.Th><Table.Th>Criado</Table.Th>
                  <Table.Th ta="right">Espaço</Table.Th><Table.Th /></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {cases.map((c) => (
                  <Table.Tr key={c.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/cases/${c.id}`)}>
                    <Table.Td>{c.id}</Table.Td>
                    <Table.Td fw={600}>
                      <Group gap="xs" wrap="nowrap">{c.name}
                        {!!c.alerts_new && (
                          <Tooltip label="Alertas de pessoas do processo por rever">
                            <Badge size="sm" color="orange" leftSection={<IconBellRinging size={10} />}>{c.alerts_new}</Badge>
                          </Tooltip>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>{c.description}</Table.Td>
                    <Table.Td>{c.analyst}</Table.Td>
                    <Table.Td>{formatDate(c.created_at)}</Table.Td>
                    <Table.Td ta="right" style={{ whiteSpace: "nowrap" }}>
                      {c.size_bytes !== undefined ? formatBytes(c.size_bytes) : "—"}</Table.Td>
                    <Table.Td w={40} onClick={(e) => e.stopPropagation()}>
                      <Tooltip label="Eliminar processo">
                        <ActionIcon variant="subtle" color="red" aria-label={`Eliminar processo ${c.id}`} onClick={() => setToDelete(c)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
              <Table.Tfoot>
                <Table.Tr>
                  <Table.Td colSpan={5}><Text size="sm" c="dimmed">{cases.length} processo(s)</Text></Table.Td>
                  <Table.Td ta="right" style={{ whiteSpace: "nowrap" }}>
                    <Text size="sm" fw={600}>{formatBytes(cases.reduce((t, c) => t + (c.size_bytes ?? 0), 0))}</Text>
                  </Table.Td>
                  <Table.Td />
                </Table.Tr>
              </Table.Tfoot>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
      <DeleteCaseModal kase={toDelete} onClose={() => setToDelete(null)}
        onDeleted={(c) => { setToDelete(null); setCases((cs) => cs.filter((x) => x.id !== c.id)); }} />
      <Modal opened={opened} onClose={close} title="Novo processo">
        <Stack>
          <TextInput label="Nome / referência do processo" required value={name}
            onChange={(e) => setName(e.currentTarget.value)} data-autofocus />
          <Textarea label="Descrição" autosize minRows={3} value={description}
            onChange={(e) => setDescription(e.currentTarget.value)} />
          <Button onClick={create} disabled={!name.trim()}>Criar</Button>
        </Stack>
      </Modal>
    </Stack>
  );
}

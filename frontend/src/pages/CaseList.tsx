import { ActionIcon, Button, Card, Group, Modal, Stack, Table, Text, TextInput, Textarea, Title, Tooltip } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconFolderPlus, IconTrash } from "@tabler/icons-react";
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
        <Title order={2}>Casos</Title>
        <Button leftSection={<IconFolderPlus size={18} />} onClick={open}>Novo caso</Button>
      </Group>
      <Card withBorder p={0}>
        {cases.length === 0 ? (
          <Text c="dimmed" p="lg">Ainda não existem casos. Crie um para importar imagens de referência e vídeos.</Text>
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
                    <Table.Td fw={600}>{c.name}</Table.Td>
                    <Table.Td>{c.description}</Table.Td>
                    <Table.Td>{c.analyst}</Table.Td>
                    <Table.Td>{formatDate(c.created_at)}</Table.Td>
                    <Table.Td ta="right" style={{ whiteSpace: "nowrap" }}>
                      {c.size_bytes !== undefined ? formatBytes(c.size_bytes) : "—"}</Table.Td>
                    <Table.Td w={40} onClick={(e) => e.stopPropagation()}>
                      <Tooltip label="Eliminar caso">
                        <ActionIcon variant="subtle" color="red" aria-label={`Eliminar caso ${c.id}`} onClick={() => setToDelete(c)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
              <Table.Tfoot>
                <Table.Tr>
                  <Table.Td colSpan={5}><Text size="sm" c="dimmed">{cases.length} caso(s)</Text></Table.Td>
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
      <Modal opened={opened} onClose={close} title="Novo caso">
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

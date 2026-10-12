import { ActionIcon, Badge, Card, Group, Image, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IMAGE_MIME_TYPE } from "@mantine/dropzone";
import { IconPhoto, IconTrash } from "@tabler/icons-react";
import { useState } from "react";

import { api } from "../api";
import { notifyError, notifyOk } from "../notify";
import type { ReferenceImage } from "../types";
import UploadZone from "./UploadZone";

interface Props {
  caseId: number;
  references: ReferenceImage[];
  onChange: () => void;
}

export default function ReferencesPanel({ caseId, references, onChange }: Props) {
  const [progress, setProgress] = useState<number | null>(null);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setProgress(0);
    try {
      const created = await api.uploadReferences(caseId, files, setProgress);
      const invalid = created.filter((r) => !r.valid).length;
      notifyOk(`${created.length} imagem(ns) importada(s)${invalid ? `, ${invalid} inválida(s)` : ""}.`);
    } catch (e) {
      notifyError(e, "Falha ao importar imagens");
    } finally {
      setProgress(null);
      onChange();
    }
  };

  const remove = async (id: number) => {
    try {
      await api.deleteReference(id);
      onChange();
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <Stack>
      <UploadZone accept={IMAGE_MIME_TYPE} onDrop={upload} progress={progress}
        icon={<IconPhoto size={40} stroke={1.5} />}
        title="Arraste imagens da pessoa a procurar"
        hint="Uma face por imagem. Várias imagens (ângulos e iluminação diferentes) melhoram a deteção." />
      <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }}>
        {references.map((r) => (
          <Card key={r.id} withBorder padding="xs">
            <Card.Section>
              <Image src={api.referenceImageUrl(r.id, !!r.face_crop_path)} h={160} fit="cover" alt={r.filename} />
            </Card.Section>
            <Group justify="space-between" mt="xs" wrap="nowrap">
              <Text size="xs" truncate title={r.filename}>{r.filename}</Text>
              <Tooltip label="Remover">
                <ActionIcon variant="subtle" color="red" size="sm" onClick={() => remove(r.id)} aria-label="Remover">
                  <IconTrash size={14} />
                </ActionIcon>
              </Tooltip>
            </Group>
            <Tooltip label={r.message} multiline w={240}>
              <Badge color={r.valid ? "green" : "red"} variant="light" fullWidth mt={4}>
                {r.valid ? "Válida" : `Inválida (${r.face_count} faces)`}
              </Badge>
            </Tooltip>
          </Card>
        ))}
      </SimpleGrid>
    </Stack>
  );
}

import { ActionIcon, Autocomplete, Badge, Card, Group, Image, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IMAGE_MIME_TYPE } from "@mantine/dropzone";
import { IconPhoto, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";

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

  const labels = [...new Set(references.map((r) => r.person_label).filter(Boolean))].sort();
  const groups = new Map<string, number>();
  for (const r of references.filter((x) => x.valid)) groups.set(r.person_label, (groups.get(r.person_label) ?? 0) + 1);

  return (
    <Stack>
      <UploadZone accept={IMAGE_MIME_TYPE} onDrop={upload} progress={progress}
        icon={<IconPhoto size={40} stroke={1.5} />}
        title="Arraste imagens da pessoa a procurar"
        hint="Uma face por imagem. Várias imagens (ângulos e iluminação diferentes) melhoram a deteção." />
      {references.length > 0 && (
        <Text size="sm" c="dimmed">
          Para procurar <b>várias pessoas na mesma análise</b>, dê o mesmo rótulo às imagens de cada pessoa
          (ex.: "Suspeito A", "Suspeito B"). Imagens sem rótulo são a mesma pessoa.
          {groups.size > 1 && <> Pessoas: {[...groups].map(([l, n]) => `${l || "sem rótulo"} (${n})`).join(" · ")}.</>}
        </Text>
      )}
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
            <PersonLabel reference={r} labels={labels} onSaved={onChange} />
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

function PersonLabel({ reference, labels, onSaved }: { reference: ReferenceImage; labels: string[]; onSaved: () => void }) {
  const [value, setValue] = useState(reference.person_label);
  useEffect(() => setValue(reference.person_label), [reference.person_label]);
  const save = async (v: string) => {
    const label = v.trim().replace(/\s+/g, " ");
    if (label === reference.person_label) return;
    try {
      await api.labelReference(reference.id, label);
      onSaved();
    } catch (e) {
      notifyError(e);
    }
  };
  return (
    <Autocomplete size="xs" mt={4} placeholder="Pessoa (rótulo)" aria-label={`Pessoa de ${reference.filename}`}
      data={labels} value={value} maxLength={60} onChange={setValue}
      onOptionSubmit={(v) => { setValue(v); void save(v); }}
      onBlur={() => void save(value)}
      onKeyDown={(e) => { if (e.key === "Enter") void save(value); }} />
  );
}

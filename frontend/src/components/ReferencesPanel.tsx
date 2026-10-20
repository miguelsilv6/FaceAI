import {
  ActionIcon, Badge, Button, Card, Group, Image, Menu, Modal, Progress, SimpleGrid, Stack, Text, Textarea, TextInput,
  Title, Tooltip,
} from "@mantine/core";
import { Dropzone, IMAGE_MIME_TYPE } from "@mantine/dropzone";
import {
  IconChevronLeft, IconChevronRight, IconDots, IconHelp, IconPhoto, IconPhotoPlus, IconPlus, IconTrash, IconUser,
  IconUsersGroup,
} from "@tabler/icons-react";
import { useEffect, useState, type DragEvent } from "react";

import { api } from "../api";
import { confirmAction } from "../confirm";
import { notifyError, notifyOk } from "../notify";
import { personColor } from "../personColors";
import type { CasePerson, ReferenceImage } from "../types";
import SimilarModal, { type SimilarQuery } from "./SimilarModal";
import UploadZone from "./UploadZone";

interface Props {
  caseId: number;
  references: ReferenceImage[];
  persons: CasePerson[];
  canManage?: boolean; // apagar fotos e pessoas: só o responsável ou um administrador
  onChange: () => void;
}

const DRAG_TYPE = "application/x-faceai-reference";

/** Pessoas do caso (cada uma com as suas fotos, de vários ângulos) e fotos soltas. */
export default function ReferencesPanel({ caseId, references, persons, onChange, canManage = true }: Props) {
  const [progress, setProgress] = useState<{ target: number | null; value: number } | null>(null);
  const [editing, setEditing] = useState<CasePerson | "new" | null>(null);
  const [dropTarget, setDropTarget] = useState<number | "loose" | null>(null);
  const [search, setSearch] = useState<SimilarQuery | null>(null);

  const upload = async (files: File[], personId: number | null) => {
    if (!files.length) return;
    setProgress({ target: personId, value: 0 });
    try {
      const created = await api.uploadReferences(caseId, files, (v) => setProgress({ target: personId, value: v }));
      if (personId != null) await Promise.all(created.map((r) => api.setReferencePerson(r.id, personId)));
      const invalid = created.filter((r) => !r.valid).length;
      notifyOk(`${created.length} foto(s) importada(s)${invalid ? `, ${invalid} inválida(s)` : ""}.`);
    } catch (e) {
      notifyError(e, "Falha ao importar imagens");
    } finally {
      setProgress(null);
      onChange();
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      notifyError(e);
    } finally {
      onChange();
    }
  };

  const move = (refId: number, personId: number | null) => act(() => api.setReferencePerson(refId, personId));
  const onDrop = (e: DragEvent, personId: number | null) => {
    const id = Number(e.dataTransfer.getData(DRAG_TYPE));
    setDropTarget(null);
    if (id) {
      e.preventDefault();
      void move(id, personId);
    }
  };
  const dropProps = (key: number | "loose", personId: number | null) => ({
    onDragOver: (e: DragEvent) => {
      if (e.dataTransfer.types.includes(DRAG_TYPE)) {
        e.preventDefault();
        setDropTarget(key);
      }
    },
    onDragLeave: () => setDropTarget((t) => (t === key ? null : t)),
    onDrop: (e: DragEvent) => onDrop(e, personId),
  });

  const loose = references.filter((r) => r.case_person_id == null);
  // Foto em tamanho grande (clicar na miniatura); ← / → percorrem as fotos do mesmo grupo (pessoa ou soltas)
  const [viewing, setViewing] = useState<ReferenceImage | null>(null);
  const siblings = viewing ? references.filter((r) => r.case_person_id === viewing.case_person_id) : [];
  const thumb = (r: ReferenceImage) => (
    <RefThumb key={r.id} reference={r} persons={persons} onMove={move} onOpen={setViewing}
      onSearch={(ref) => setSearch({ kind: "reference", id: ref.id, name: ref.filename })}
      onDelete={canManage ? async (ref) => {
        if (await confirmAction({ title: "Eliminar esta foto?", message: ref.filename, confirmLabel: "Eliminar", danger: true })) {
          void act(() => api.deleteReference(ref.id));
        }
      } : undefined} />
  );

  return (
    <Stack>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <Tooltip multiline w={380} withArrow position="bottom-start" label={<>
          Para uma pessoa com várias fotos (ângulos, iluminação, com/sem óculos), crie uma <b>pessoa</b> e junte-lhe as
          fotos. Cada face do vídeo é comparada com todas as fotos da pessoa e conta a mais parecida. Com várias pessoas
          selecionadas, a análise procura-as todas na mesma passagem. Fotos soltas contam como uma só pessoa.
          Pode arrastar uma foto para outra pessoa.
        </>}>
          <ActionIcon variant="light" radius="xl" size="lg" aria-label="Ajuda"><IconHelp size={18} /></ActionIcon>
        </Tooltip>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing("new")}>Nova pessoa</Button>
      </Group>

      {persons.map((p) => {
        const refs = references.filter((r) => r.case_person_id === p.id);
        return (
          <Card key={p.id} withBorder {...dropProps(p.id, p.id)}
            style={dropTarget === p.id ? { outline: "2px dashed var(--mantine-color-blue-5)" } : undefined}>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Group gap="xs" wrap="nowrap">
                <IconUser size={20} color={`var(--mantine-color-${personColor(p.id)}-6)`} />
                <Title order={5}>{p.name}</Title>
                <Badge variant="light" color={p.valid_references ? personColor(p.id) : "red"}>
                  {refs.length} foto(s) · {p.valid_references} válida(s)
                </Badge>
              </Group>
              <Menu position="bottom-end" withinPortal>
                <Menu.Target>
                  <ActionIcon variant="subtle" aria-label={`Opções de ${p.name}`}><IconDots size={16} /></ActionIcon>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item leftSection={<IconUsersGroup size={14} />} disabled={!p.valid_references}
                    onClick={() => setSearch({ kind: "casePerson", id: p.id, name: p.name, photos: p.valid_references })}>
                    Procurar noutros processos
                  </Menu.Item>
                  <Menu.Divider />
                  <Menu.Item onClick={() => setEditing(p)}>Mudar nome / notas</Menu.Item>
                  {canManage && <Menu.Item color="red" onClick={async () => {
                    if (await confirmAction({ title: `Eliminar a pessoa "${p.name}"?`, danger: true,
                      message: "As fotos não são apagadas: passam a fotos sem pessoa.", confirmLabel: "Eliminar" })) {
                      void act(() => api.deletePerson(p.id));
                    }
                  }}>Eliminar pessoa</Menu.Item>}
                </Menu.Dropdown>
              </Menu>
            </Group>
            {p.notes && <Text size="sm" c="dimmed" mb="xs">{p.notes}</Text>}
            <SimpleGrid cols={{ base: 2, sm: 4, md: 6 }} spacing="xs">
              {refs.map(thumb)}
              <Dropzone onDrop={(f) => upload(f, p.id)} accept={IMAGE_MIME_TYPE} multiple maxSize={Infinity}
                loading={progress?.target === p.id} aria-label={`Adicionar fotos a ${p.name}`}>
                <Stack align="center" justify="center" gap={4} mih={150} style={{ pointerEvents: "none" }}>
                  <IconPhotoPlus size={28} stroke={1.5} />
                  <Text size="xs" ta="center">Adicionar fotos de {p.name}</Text>
                </Stack>
              </Dropzone>
            </SimpleGrid>
            {progress?.target === p.id && <Progress mt="xs" value={progress.value * 100} animated striped />}
          </Card>
        );
      })}

      <Card withBorder {...dropProps("loose", null)}
        style={dropTarget === "loose" ? { outline: "2px dashed var(--mantine-color-blue-5)" } : undefined}>
        <Title order={5} mb="xs">{persons.length ? "Fotos soltas" : "Fotos da pessoa a procurar"}</Title>
        <UploadZone accept={IMAGE_MIME_TYPE} onDrop={(f) => upload(f, null)}
          progress={progress?.target === null ? progress.value : null}
          icon={<IconPhoto size={40} stroke={1.5} />}
          title={persons.length ? "Arraste fotos soltas" : "Arraste imagens da pessoa a procurar"}
          hint="Uma face por imagem. Várias imagens (ângulos e iluminação diferentes) melhoram a deteção." />
        {loose.length > 0 && <SimpleGrid cols={{ base: 2, sm: 4, md: 6 }} spacing="xs" mt="sm">{loose.map(thumb)}</SimpleGrid>}
      </Card>

      <SimilarModal query={search} defaultThreshold={0.5} onClose={() => setSearch(null)} />
      <RefViewer reference={viewing} siblings={siblings} persons={persons} onShow={setViewing}
        onClose={() => setViewing(null)} />
      <PersonModal caseId={caseId} person={editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); onChange(); }} />
    </Stack>
  );
}

/** Foto de referência em tamanho grande (imagem completa) e o recorte da face usado na comparação. */
function RefViewer({ reference: r, siblings, persons, onShow, onClose }: {
  reference: ReferenceImage | null; siblings: ReferenceImage[]; persons: CasePerson[];
  onShow: (r: ReferenceImage) => void; onClose: () => void;
}) {
  const index = r ? siblings.findIndex((x) => x.id === r.id) : -1;
  const go = (d: number) => {
    if (index < 0 || siblings.length < 2) return;
    onShow(siblings[(index + d + siblings.length) % siblings.length]);
  };
  useEffect(() => {
    if (!r) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const owner = r?.case_person_id != null ? persons.find((p) => p.id === r.case_person_id)?.name
    : persons.length ? "Fotos soltas" : "Fotos da pessoa a procurar";
  return (
    <Modal opened={!!r} onClose={onClose} size="xl" centered
      title={r ? `${owner ?? ""} · ${r.filename}${siblings.length > 1 ? ` (${index + 1}/${siblings.length})` : ""}` : ""}>
      {r && (
        <Stack gap="xs">
          <Group align="flex-start" wrap="nowrap" gap="sm">
            <Image src={api.referenceImageUrl(r.id)} alt={r.filename} mah="70vh" fit="contain" bg="black" radius="sm"
              style={{ flex: 1, minWidth: 0 }} data-testid="reference-large" />
            {r.face_crop_path && (
              <Stack gap={4} w={160}>
                <Image src={api.referenceImageUrl(r.id, true)} alt="Recorte da face" w={160} radius="sm" />
                <Text size="xs" c="dimmed" ta="center">face usada na comparação</Text>
              </Stack>
            )}
          </Group>
          <Group justify="space-between">
            <Badge color={r.valid ? "green" : "red"} variant="light">
              {r.valid ? "Válida" : `Inválida: ${r.message || `${r.face_count} faces`}`}
            </Badge>
            {siblings.length > 1 && (
              <Group gap="xs">
                <ActionIcon variant="default" aria-label="Foto anterior" onClick={() => go(-1)}><IconChevronLeft size={16} /></ActionIcon>
                <ActionIcon variant="default" aria-label="Foto seguinte" onClick={() => go(1)}><IconChevronRight size={16} /></ActionIcon>
              </Group>
            )}
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

function RefThumb({ reference: r, persons, onMove, onSearch, onDelete, onOpen }: {
  reference: ReferenceImage; persons: CasePerson[];
  onMove: (refId: number, personId: number | null) => void; onSearch: (r: ReferenceImage) => void;
  onDelete?: (r: ReferenceImage) => void; onOpen: (r: ReferenceImage) => void;
}) {
  return (
    <Card withBorder padding={4} draggable onDragStart={(e) => {
      e.dataTransfer.setData(DRAG_TYPE, String(r.id));
      e.dataTransfer.effectAllowed = "move";
    }} style={{ cursor: "grab" }}>
      <Card.Section style={{ cursor: "zoom-in" }} onClick={() => onOpen(r)} title="Abrir a foto em tamanho grande">
        <Image loading="lazy" decoding="async" src={api.referenceImageUrl(r.id, !!r.face_crop_path)} h={110} fit="cover" alt={r.filename} draggable={false} />
      </Card.Section>
      <Group justify="space-between" mt={4} gap={2} wrap="nowrap">
        <Tooltip label={r.message || r.filename} multiline w={240}>
          <Badge color={r.valid ? "green" : "red"} variant="light" size="xs">
            {r.valid ? "Válida" : `Inválida (${r.face_count})`}
          </Badge>
        </Tooltip>
        <Menu position="bottom-end" withinPortal>
          <Menu.Target>
            <ActionIcon variant="subtle" size="xs" aria-label={`Opções da foto ${r.filename}`}><IconDots size={14} /></ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Mover para</Menu.Label>
            {persons.filter((p) => p.id !== r.case_person_id).map((p) => (
              <Menu.Item key={p.id} onClick={() => onMove(r.id, p.id)}>{p.name}</Menu.Item>
            ))}
            {r.case_person_id != null && <Menu.Item onClick={() => onMove(r.id, null)}>Fotos soltas (tirar da pessoa)</Menu.Item>}
            <Menu.Divider />
            <Menu.Item leftSection={<IconUsersGroup size={14} />} disabled={!r.valid} onClick={() => onSearch(r)}>
              Procurar esta foto noutros processos
            </Menu.Item>
            {onDelete && <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => onDelete(r)}>
              Eliminar foto</Menu.Item>}
          </Menu.Dropdown>
        </Menu>
      </Group>
      <Text size="xs" truncate title={r.filename}>{r.source_detection_id ? `Do CCTV (deteção #${r.source_detection_id})` : r.filename}</Text>
    </Card>
  );
}

function PersonModal({ caseId, person, onClose, onSaved }: {
  caseId: number; person: CasePerson | "new" | null; onClose: () => void; onSaved: () => void;
}) {
  const isNew = person === "new";
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [key, setKey] = useState<string>("");
  const current = person === null ? "" : isNew ? "new" : String(person.id);
  if (current !== key) { // abrir o modal: carrega os valores da pessoa
    setKey(current);
    setName(person && !isNew ? person.name : "");
    setNotes(person && !isNew ? person.notes : "");
  }
  const save = async () => {
    try {
      if (isNew) await api.createPerson(caseId, name, notes);
      else if (person) await api.updatePerson(person.id, { name, notes });
      onSaved();
    } catch (e) {
      notifyError(e);
    }
  };
  return (
    <Modal opened={person !== null} onClose={onClose} title={isNew ? "Nova pessoa" : "Editar pessoa"}>
      <Stack>
        <TextInput label="Nome" placeholder="ex.: Suspeito A" required maxLength={80} value={name} data-autofocus
          onChange={(e) => setName(e.currentTarget.value)} />
        <Textarea label="Notas" placeholder="ex.: casaco azul, tatuagem no pulso direito" autosize minRows={2}
          maxLength={2000} value={notes} onChange={(e) => setNotes(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={!name.trim()}>{isNew ? "Criar" : "Guardar"}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

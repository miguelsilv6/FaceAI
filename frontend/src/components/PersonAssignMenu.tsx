import { ActionIcon, Button, Menu, Tooltip } from "@mantine/core";
import { IconUserCheck, IconUserPlus } from "@tabler/icons-react";

import type { CasePerson, ReferenceImage } from "../types";

export interface PersonAssignProps {
  detectionId: number;
  persons: CasePerson[];
  /** Foto de referência já criada a partir desta deteção (se existir). */
  existing?: ReferenceImage;
  onAdd: (detectionId: number, personId: number | "new" | null) => void;
  onRemove: (ref: ReferenceImage) => void;
  compact?: boolean;
}

/** "Adicionar esta face à pessoa…" / "Remover da pessoa" (faces do próprio CCTV como fotos de referência). */
export default function PersonAssignMenu({ detectionId, persons, existing, onAdd, onRemove, compact = true }: PersonAssignProps) {
  const owner = existing ? persons.find((p) => p.id === existing.case_person_id) : undefined;
  const label = existing ? `Esta face é foto de referência${owner ? ` de ${owner.name}` : " (solta)"}` : "Adicionar esta face a uma pessoa";
  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        {compact ? (
          <Tooltip label={label}>
            <ActionIcon variant={existing ? "light" : "subtle"} color={existing ? "green" : undefined} size="sm"
              aria-label={label}>
              {existing ? <IconUserCheck size={16} /> : <IconUserPlus size={16} />}
            </ActionIcon>
          </Tooltip>
        ) : (
          <Button size="xs" variant="subtle" leftSection={existing ? <IconUserCheck size={16} /> : <IconUserPlus size={16} />}>
            {existing ? `Referência${owner ? ` de ${owner.name}` : ""}` : "Adicionar à pessoa…"}
          </Button>
        )}
      </Menu.Target>
      <Menu.Dropdown>
        {existing ? (
          <>
            <Menu.Label>{label}</Menu.Label>
            <Menu.Item color="red" onClick={() => onRemove(existing)}>
              Remover {owner ? `de ${owner.name}` : "das fotos de referência"}
            </Menu.Item>
          </>
        ) : (
          <>
            <Menu.Label>Adicionar esta face (recorte) a</Menu.Label>
            {persons.map((p) => <Menu.Item key={p.id} onClick={() => onAdd(detectionId, p.id)}>{p.name}</Menu.Item>)}
            <Menu.Item onClick={() => onAdd(detectionId, "new")}>Nova pessoa…</Menu.Item>
            <Menu.Divider />
            <Menu.Item onClick={() => onAdd(detectionId, null)}>Fotos soltas</Menu.Item>
          </>
        )}
      </Menu.Dropdown>
    </Menu>
  );
}

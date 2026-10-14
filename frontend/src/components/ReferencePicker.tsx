import {
  Combobox, Group, HoverCard, Image, Input, Pill, PillsInput, Text, useCombobox,
} from "@mantine/core";
import { IconCheck, IconPhoto, IconUser } from "@tabler/icons-react";
import type { ReactNode } from "react";

import { api } from "../api";
import { personColor } from "../personColors";
import type { CasePerson, ReferenceImage } from "../types";

/** Valor de uma escolha: "p:<id>" = pessoa do caso (todas as suas fotos válidas); "r:<id>" = foto solta. */
export type RefChoice = string;

/** Todas as escolhas possíveis (pessoas com fotos válidas e fotos soltas válidas). */
export function allChoices(refs: ReferenceImage[], persons: CasePerson[]): RefChoice[] {
  return [
    ...persons.filter((p) => refs.some((r) => r.case_person_id === p.id)).map((p) => `p:${p.id}`),
    ...refs.filter((r) => r.case_person_id == null).map((r) => `r:${r.id}`),
  ];
}

/** Ids das fotos de referência que as escolhas representam. */
export function choicesToRefIds(choices: RefChoice[], refs: ReferenceImage[]): number[] {
  const ids = new Set<number>();
  for (const c of choices) {
    const id = Number(c.slice(2));
    if (c.startsWith("p:")) refs.filter((r) => r.case_person_id === id).forEach((r) => ids.add(r.id));
    else if (refs.some((r) => r.id === id)) ids.add(id);
  }
  return [...ids];
}

function Thumb({ refImage, children }: { refImage: ReferenceImage; children: ReactNode }) {
  return (
    <HoverCard openDelay={150} shadow="md" withinPortal position="top">
      <HoverCard.Target>{children}</HoverCard.Target>
      <HoverCard.Dropdown p={4}>
        <Image src={api.referenceImageUrl(refImage.id, !!refImage.face_crop_path)} w={130} h={130} fit="cover"
          radius="sm" alt={refImage.filename} />
        <Text size="xs" ta="center" mt={2} maw={130} truncate>{refImage.filename}</Text>
      </HoverCard.Dropdown>
    </HoverCard>
  );
}

/**
 * Escolha das amostras da análise: pessoas do caso (uma pill colorida "Pessoa A", que leva todas as suas fotos)
 * e fotos soltas (pill neutra com o nome; ao passar o rato mostra a miniatura).
 */
export default function ReferencePicker({ refs, persons, value, onChange, description, placeholder }: {
  refs: ReferenceImage[]; persons: CasePerson[]; value: RefChoice[]; onChange: (v: RefChoice[]) => void;
  description?: ReactNode; placeholder?: string;
}) {
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() });
  const withRefs = persons.filter((p) => refs.some((r) => r.case_person_id === p.id));
  const loose = refs.filter((r) => r.case_person_id == null);
  const toggle = (v: RefChoice) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);

  const pills = value.map((v) => {
    const id = Number(v.slice(2));
    if (v.startsWith("p:")) {
      const p = persons.find((x) => x.id === id);
      if (!p) return null;
      return (
        <Pill key={v} withRemoveButton onRemove={() => toggle(v)} aria-label={`Pessoa ${p.name}`}
          style={{ background: `var(--mantine-color-${personColor(p.id)}-6)`, color: "white", fontWeight: 600 }}>
          <Group gap={4} wrap="nowrap"><IconUser size={12} />{p.name}</Group>
        </Pill>
      );
    }
    const r = refs.find((x) => x.id === id);
    if (!r) return null;
    return (
      <Thumb key={v} refImage={r}>
        <Pill withRemoveButton onRemove={() => toggle(v)} aria-label={`Foto ${r.filename}`}>
          <Group gap={4} wrap="nowrap"><IconPhoto size={12} />{r.filename}</Group>
        </Pill>
      </Thumb>
    );
  });

  return (
    <Combobox store={combobox} onOptionSubmit={toggle} withinPortal>
      <Combobox.DropdownTarget>
        <PillsInput label="Pessoas e fotos de referência" description={description} pointer
          onClick={() => combobox.toggleDropdown()}>
          <Pill.Group>
            {pills.length ? pills : <Input.Placeholder>{placeholder}</Input.Placeholder>}
            <Combobox.EventsTarget>
              <PillsInput.Field type="hidden" onBlur={() => combobox.closeDropdown()}
                onKeyDown={(e) => {
                  if (e.key === "Backspace" && value.length) onChange(value.slice(0, -1));
                }} />
            </Combobox.EventsTarget>
          </Pill.Group>
        </PillsInput>
      </Combobox.DropdownTarget>
      <Combobox.Dropdown>
        <Combobox.Options mah={320} style={{ overflowY: "auto" }}>
          {withRefs.length > 0 && (
            <Combobox.Group label="Pessoas">
              {withRefs.map((p) => {
                const n = refs.filter((r) => r.case_person_id === p.id).length;
                return (
                  <Combobox.Option key={p.id} value={`p:${p.id}`} active={value.includes(`p:${p.id}`)}>
                    <Group gap="xs" wrap="nowrap">
                      {value.includes(`p:${p.id}`) ? <IconCheck size={14} /> : <span style={{ width: 14 }} />}
                      <IconUser size={16} color={`var(--mantine-color-${personColor(p.id)}-6)`} />
                      <Text size="sm" fw={600} c={`${personColor(p.id)}.7`}>{p.name}</Text>
                      <Text size="xs" c="dimmed">{n} foto(s)</Text>
                    </Group>
                  </Combobox.Option>
                );
              })}
            </Combobox.Group>
          )}
          {loose.length > 0 && (
            <Combobox.Group label={withRefs.length ? "Fotos soltas" : "Fotos"}>
              {loose.map((r) => (
                <Combobox.Option key={r.id} value={`r:${r.id}`} active={value.includes(`r:${r.id}`)}>
                  <Group gap="xs" wrap="nowrap">
                    {value.includes(`r:${r.id}`) ? <IconCheck size={14} /> : <span style={{ width: 14 }} />}
                    <Thumb refImage={r}>
                      <Image src={api.referenceImageUrl(r.id, !!r.face_crop_path)} w={28} h={28} radius="sm" fit="cover"
                        alt="" />
                    </Thumb>
                    <Text size="sm" truncate>#{r.id} {r.filename}</Text>
                  </Group>
                </Combobox.Option>
              ))}
            </Combobox.Group>
          )}
          {!withRefs.length && !loose.length && <Combobox.Empty>Sem amostras válidas.</Combobox.Empty>}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}

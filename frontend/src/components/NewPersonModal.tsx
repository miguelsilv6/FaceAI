import { Button, Group, Modal, Stack, TextInput } from "@mantine/core";
import { useState } from "react";

/** Criar uma pessoa do caso a partir de uma face dos resultados. */
export default function NewPersonModal({ opened, onClose, onCreate }: {
  opened: boolean; onClose: () => void; onCreate: (name: string) => void;
}) {
  const [name, setName] = useState("");
  return (
    <Modal opened={opened} onClose={onClose} title="Nova pessoa com esta face">
      <Stack>
        <TextInput label="Nome" placeholder="ex.: Suspeito A" maxLength={80} value={name} data-autofocus
          onChange={(e) => setName(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancelar</Button>
          <Button disabled={!name.trim()} onClick={() => { onCreate(name.trim()); setName(""); }}>Criar</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

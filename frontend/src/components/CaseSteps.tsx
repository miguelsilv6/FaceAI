import { Card, Group, Stack, Text, ThemeIcon, UnstyledButton } from "@mantine/core";
import { IconCheck } from "@tabler/icons-react";

export interface Step {
  label: string;
  hint: string;
  done: boolean;
  tab: string;
}

/** Primeiros passos do processo, pela ordem do trabalho; cada passo abre o separador respetivo. */
export default function CaseSteps({ steps, onOpenTab }: { steps: Step[]; onOpenTab: (tab: string) => void }) {
  if (steps.every((s) => s.done)) return null;
  const next = steps.findIndex((s) => !s.done);
  return (
    <Card withBorder>
      <Text fw={600} mb="xs">Primeiros passos</Text>
      <Group gap="sm" grow align="stretch" wrap="wrap">
        {steps.map((s, i) => (
          <UnstyledButton key={s.label} onClick={() => onOpenTab(s.tab)} miw={200} p="xs"
            style={{ borderRadius: 8, border: `1px solid var(--mantine-color-${i === next ? "blue-5" : "default-border"})` }}>
            <Group gap="sm" wrap="nowrap" align="flex-start">
              <ThemeIcon radius="xl" size="md" variant={s.done ? "filled" : "light"} color={s.done ? "green" : i === next ? "blue" : "gray"}>
                {s.done ? <IconCheck size={14} /> : <Text size="xs" fw={700}>{i + 1}</Text>}
              </ThemeIcon>
              <Stack gap={0}>
                <Text size="sm" fw={500} td={s.done ? "line-through" : undefined} c={s.done ? "dimmed" : undefined}>{s.label}</Text>
                <Text size="xs" c="dimmed">{s.hint}</Text>
              </Stack>
            </Group>
          </UnstyledButton>
        ))}
      </Group>
    </Card>
  );
}

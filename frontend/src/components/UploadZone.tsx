import { Group, Progress, Stack, Text } from "@mantine/core";
import { Dropzone, type DropzoneProps } from "@mantine/dropzone";
import { IconUpload } from "@tabler/icons-react";
import type { ReactNode } from "react";

interface Props {
  accept: DropzoneProps["accept"];
  onDrop: (files: File[]) => void;
  progress: number | null;
  title: string;
  hint: string;
  icon: ReactNode;
  compact?: boolean;
}

export default function UploadZone({ accept, onDrop, progress, title, hint, icon, compact }: Props) {
  return (
    <Stack gap={4}>
      <Dropzone onDrop={onDrop} accept={accept} loading={progress !== null} maxSize={Infinity} multiple>
        <Group justify="center" gap="lg" mih={compact ? 56 : 110} style={{ pointerEvents: "none" }}>
          <Dropzone.Accept><IconUpload size={40} /></Dropzone.Accept>
          <Dropzone.Idle>{icon}</Dropzone.Idle>
          <div>
            <Text size={compact ? "sm" : "lg"} fw={600}>{title}</Text>
            <Text size={compact ? "xs" : "sm"} c="dimmed">{hint}</Text>
          </div>
        </Group>
      </Dropzone>
      {progress !== null && <Progress value={progress * 100} animated striped />}
    </Stack>
  );
}

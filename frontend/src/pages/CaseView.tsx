import {
  ActionIcon, Anchor, Badge, Breadcrumbs, Button, Group, Modal, Stack, Tabs, Text, TextInput, Textarea, Title, Tooltip,
} from "@mantine/core";
import { IconActivity, IconHistory, IconMovie, IconPencil, IconPhoto, IconTrash } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, formatDate } from "../api";
import AuditPanel from "../components/AuditPanel";
import DeleteCaseModal from "../components/DeleteCaseModal";
import JobsPanel from "../components/JobsPanel";
import ReferencesPanel from "../components/ReferencesPanel";
import VideosPanel from "../components/VideosPanel";
import { notifyError, notifyOk } from "../notify";
import type { AuditEvent, Case, Job, ReferenceImage, Video } from "../types";

export default function CaseView() {
  const caseId = Number(useParams().caseId);
  const [kase, setCase] = useState<Case | null>(null);
  const [references, setReferences] = useState<ReferenceImage[]>([]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [tab, setTab] = useState<string | null>("references");
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  const reload = useCallback(() => {
    Promise.all([api.case(caseId), api.references(caseId), api.videos(caseId), api.jobs(caseId), api.audit(caseId)])
      .then(([c, r, v, j, a]) => {
        setCase(c);
        setReferences(r);
        setVideos(v);
        setJobs(j);
        setAudit(a);
      })
      .catch(notifyError);
  }, [caseId]);
  const reloadVideos = useCallback(() => {
    api.videos(caseId).then(setVideos).catch(() => undefined);
  }, [caseId]);
  const reloadJobs = useCallback(() => {
    api.jobs(caseId).then(setJobs).catch(() => undefined);
  }, [caseId]);

  useEffect(reload, [reload]);
  useEffect(() => {
    if (tab === "audit") api.audit(caseId).then(setAudit).catch(notifyError);
  }, [tab, caseId]);

  const startEdit = () => {
    if (!kase) return;
    setName(kase.name);
    setDescription(kase.description);
    setEditing(true);
  };
  const save = async () => {
    setSaving(true);
    try {
      const updated = await api.updateCase(caseId, { name: name.trim(), description });
      setCase(updated);
      setEditing(false);
      notifyOk("Caso atualizado. A alteração fica registada na auditoria.");
      if (tab === "audit") api.audit(caseId).then(setAudit);
    } catch (e) {
      notifyError(e, "Não foi possível guardar");
    } finally {
      setSaving(false);
    }
  };

  if (!kase) return null;
  return (
    <Stack maw={1300} mx="auto">
      <Breadcrumbs>
        <Anchor component={Link} to="/">Casos</Anchor>
        <Text>#{kase.id} {kase.name}</Text>
      </Breadcrumbs>
      <div>
        <Group gap="xs" wrap="nowrap">
          <Title order={2}>{kase.name}</Title>
          <Tooltip label="Editar nome e descrição do caso">
            <ActionIcon variant="subtle" onClick={startEdit} aria-label="Editar caso"><IconPencil size={18} /></ActionIcon>
          </Tooltip>
          <Tooltip label="Eliminar o caso (vídeos, cópias, referências e análises)">
            <ActionIcon variant="subtle" color="red" onClick={() => setDeleting(true)} aria-label="Eliminar caso">
              <IconTrash size={18} />
            </ActionIcon>
          </Tooltip>
        </Group>
        <Text c="dimmed" size="sm">{kase.description || "Sem descrição"} · criado em {formatDate(kase.created_at)}
          {kase.analyst ? ` por ${kase.analyst}` : ""}</Text>
      </div>
      <DeleteCaseModal kase={deleting ? kase : null} onClose={() => setDeleting(false)} onDeleted={() => navigate("/")} />
      <Modal opened={editing} onClose={() => setEditing(false)} title={`Editar caso #${kase.id}`}>
        <Stack>
          <TextInput label="Nome / referência do processo" required value={name} data-autofocus
            onChange={(e) => setName(e.currentTarget.value)} error={!name.trim() ? "Obrigatório" : undefined} />
          <Textarea label="Descrição" autosize minRows={2} value={description}
            onChange={(e) => setDescription(e.currentTarget.value)} />
          <Text size="xs" c="dimmed">Os valores anteriores ficam registados na auditoria do caso.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(false)}>Cancelar</Button>
            <Button onClick={save} loading={saving} disabled={!name.trim()}>Guardar</Button>
          </Group>
        </Stack>
      </Modal>
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="references" leftSection={<IconPhoto size={16} />}>
            <Group gap={6}>Referências <Badge size="sm" variant="light">{references.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="videos" leftSection={<IconMovie size={16} />}>
            <Group gap={6}>Vídeos <Badge size="sm" variant="light">{videos.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="jobs" leftSection={<IconActivity size={16} />}>
            <Group gap={6}>Análises <Badge size="sm" variant="light">{jobs.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="audit" leftSection={<IconHistory size={16} />}>Auditoria</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="references" pt="md">
          <ReferencesPanel caseId={caseId} references={references} onChange={reload} />
        </Tabs.Panel>
        <Tabs.Panel value="videos" pt="md">
          <VideosPanel caseId={caseId} videos={videos} onChange={reloadVideos} />
        </Tabs.Panel>
        <Tabs.Panel value="jobs" pt="md">
          <JobsPanel caseId={caseId} jobs={jobs} videos={videos} references={references} onChange={reloadJobs} />
        </Tabs.Panel>
        <Tabs.Panel value="audit" pt="md">
          <AuditPanel events={audit} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

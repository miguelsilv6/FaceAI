import {
  ActionIcon, Anchor, Badge, Breadcrumbs, Button, Group, Modal, Stack, Tabs, Text, TextInput, Textarea, Title, Tooltip,
} from "@mantine/core";
import {
  IconActivity, IconHistory, IconLayoutDashboard, IconMovie, IconPencil, IconPhoto, IconPhotoCheck, IconRoute, IconTrash,
} from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, formatDate } from "../api";
import AuditPanel from "../components/AuditPanel";
import CaseDashboard from "../components/CaseDashboard";
import { useAuth } from "../auth";
import DeleteCaseModal from "../components/DeleteCaseModal";
import JobsPanel from "../components/JobsPanel";
import RouteTimeline from "../components/RouteTimeline";
import ReferencesPanel from "../components/ReferencesPanel";
import ValidatedFramesPanel from "../components/ValidatedFramesPanel";
import VideosPanel from "../components/VideosPanel";
import { notifyError, notifyOk } from "../notify";
import type { AuditEvent, Case, CaseMember, CasePerson, Job, Location, ReferenceImage, Video } from "../types";

export default function CaseView() {
  const caseId = Number(useParams().caseId);
  const [kase, setCase] = useState<Case | null>(null);
  const [references, setReferences] = useState<ReferenceImage[]>([]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [members, setMembers] = useState<CaseMember[]>([]);
  const [persons, setPersons] = useState<CasePerson[]>([]);
  const { user, isAdmin } = useAuth();
  const [search] = useSearchParams();
  const [tab, setTab] = useState<string | null>(search.get("tab") ?? "dashboard");
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  const reload = useCallback(() => {
    Promise.all([api.case(caseId), api.references(caseId), api.videos(caseId), api.jobs(caseId), api.audit(caseId),
      api.casePersons(caseId), api.locations(caseId)])
      .then(([c, r, v, j, a, p, l]) => {
        setPersons(p);
        setLocations(l);
        setCase(c);
        setReferences(r);
        setVideos(v);
        setJobs(j);
        setAudit(a);
      })
      .catch(notifyError);
  }, [caseId]);
  const reloadVideos = useCallback(() => {
    Promise.all([api.videos(caseId), api.locations(caseId)])
      .then(([v, l]) => { setVideos(v); setLocations(l); }).catch(() => undefined);
  }, [caseId]);
  const reloadJobs = useCallback(() => {
    api.jobs(caseId).then(setJobs).catch(() => undefined);
  }, [caseId]);

  const reloadMembers = useCallback(() => {
    api.members(caseId).then(setMembers).catch(() => setMembers([]));
  }, [caseId]);
  const canManage = isAdmin || members.some((m) => m.user_id === user.id && m.role === "owner");

  useEffect(reload, [reload]);
  useEffect(reloadMembers, [reloadMembers]);
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
          {canManage && <>
          <Tooltip label="Editar nome e descrição do caso">
            <ActionIcon variant="subtle" onClick={startEdit} aria-label="Editar caso"><IconPencil size={18} /></ActionIcon>
          </Tooltip>
          <Tooltip label="Eliminar o caso (vídeos, cópias, referências e análises)">
            <ActionIcon variant="subtle" color="red" onClick={() => setDeleting(true)} aria-label="Eliminar caso">
              <IconTrash size={18} />
            </ActionIcon>
          </Tooltip>
          </>}
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
          <Tabs.Tab value="dashboard" leftSection={<IconLayoutDashboard size={16} />}>Painel</Tabs.Tab>
          <Tabs.Tab value="references" leftSection={<IconPhoto size={16} />}>
            <Group gap={6}>Referências/Pessoas <Badge size="sm" variant="light">{references.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="videos" leftSection={<IconMovie size={16} />}>
            <Group gap={6}>Locais e vídeos <Badge size="sm" variant="light">{locations.length} · {videos.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="jobs" leftSection={<IconActivity size={16} />}>
            <Group gap={6}>Análises <Badge size="sm" variant="light">{jobs.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="results" leftSection={<IconPhotoCheck size={16} />}>Resultados</Tabs.Tab>
          <Tabs.Tab value="route" leftSection={<IconRoute size={16} />}>Percurso/Multi View</Tabs.Tab>
          <Tabs.Tab value="audit" leftSection={<IconHistory size={16} />}>Auditoria</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="dashboard" pt="md">
          <CaseDashboard caseId={caseId} jobs={jobs} onOpenTab={setTab} />
        </Tabs.Panel>
        <Tabs.Panel value="references" pt="md">
          <ReferencesPanel caseId={caseId} references={references} persons={persons} onChange={reload} />
        </Tabs.Panel>
        <Tabs.Panel value="videos" pt="md">
          <VideosPanel caseId={caseId} videos={videos} locations={locations} onChange={reloadVideos} />
        </Tabs.Panel>
        <Tabs.Panel value="jobs" pt="md">
          <JobsPanel caseId={caseId} jobs={jobs} videos={videos} locations={locations} references={references} persons={persons}
            onChange={reloadJobs} />
        </Tabs.Panel>
        <Tabs.Panel value="results" pt="md">
          <ValidatedFramesPanel caseId={caseId} />
        </Tabs.Panel>
        <Tabs.Panel value="route" pt="md">
          <RouteTimeline caseId={caseId} jobs={jobs} videos={videos} locations={locations} />
        </Tabs.Panel>
        <Tabs.Panel value="audit" pt="md">
          <AuditPanel events={audit} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

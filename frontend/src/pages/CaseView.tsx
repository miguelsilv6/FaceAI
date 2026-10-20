import {
  ActionIcon, Anchor, Badge, Breadcrumbs, Button, Group, Menu, Modal, SegmentedControl, Stack, Tabs, Text, TextInput,
  Textarea, Title,
} from "@mantine/core";
import {
  IconActivity, IconDots, IconHistory, IconLayoutDashboard, IconMovie, IconPencil, IconPhotoCheck, IconTrash, IconUsers,
  IconUsersGroup,
} from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, formatDate } from "../api";
import AuditPanel from "../components/AuditPanel";
import CaseDashboard from "../components/CaseDashboard";
import { useAuth } from "../auth";
import DeleteCaseModal from "../components/DeleteCaseModal";
import JobsPanel from "../components/JobsPanel";
import MembersPanel from "../components/MembersPanel";
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
  // Separadores pela ordem do trabalho; "route" e "audit" (ligações antigas) passam a vista de Resultados e menu Mais.
  const initial = search.get("tab") ?? "dashboard";
  const [tab, setTab] = useState<string | null>(initial === "route" ? "results" : initial === "audit" ? "dashboard" : initial);
  const [resultsView, setResultsView] = useState<"images" | "route">(initial === "route" ? "route" : "images");
  const [auditOpen, setAuditOpen] = useState(initial === "audit");
  const [teamOpen, setTeamOpen] = useState(false);
  const openTab = (t: string) => {
    if (t === "audit") setAuditOpen(true);
    else if (t === "route") { setResultsView("route"); setTab("results"); }
    else setTab(t);
  };
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
    if (auditOpen) api.audit(caseId).then(setAudit).catch(notifyError);
  }, [auditOpen, caseId]);

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
      notifyOk("Processo atualizado. A alteração fica registada na auditoria.");
      if (auditOpen) api.audit(caseId).then(setAudit);
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
        <Anchor component={Link} to="/">Processos</Anchor>
        <Text>#{kase.id} {kase.name}</Text>
      </Breadcrumbs>
      <div>
        <Group gap="xs" wrap="nowrap">
          <Title order={2}>{kase.name}</Title>
          <Menu position="bottom-start" withinPortal>
            <Menu.Target>
              <ActionIcon variant="subtle" aria-label="Mais opções do processo"><IconDots size={18} /></ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item leftSection={<IconHistory size={16} />} onClick={() => setAuditOpen(true)}>Auditoria</Menu.Item>
              <Menu.Item leftSection={<IconUsersGroup size={16} />} onClick={() => setTeamOpen(true)}>Equipa</Menu.Item>
              {canManage && <>
                <Menu.Divider />
                <Menu.Item leftSection={<IconPencil size={16} />} onClick={startEdit}>Editar nome e descrição</Menu.Item>
                <Menu.Item leftSection={<IconTrash size={16} />} color="red" onClick={() => setDeleting(true)}>
                  Eliminar processo</Menu.Item>
              </>}
            </Menu.Dropdown>
          </Menu>
        </Group>
        <Text c="dimmed" size="sm">{kase.description || "Sem descrição"} · criado em {formatDate(kase.created_at)}
          {kase.analyst ? ` por ${kase.analyst}` : ""}</Text>
      </div>
      <DeleteCaseModal kase={deleting ? kase : null} onClose={() => setDeleting(false)} onDeleted={() => navigate("/")} />
      <Modal opened={editing} onClose={() => setEditing(false)} title={`Editar processo #${kase.id}`}>
        <Stack>
          <TextInput label="Nome / referência do processo" required value={name} data-autofocus
            onChange={(e) => setName(e.currentTarget.value)} error={!name.trim() ? "Obrigatório" : undefined} />
          <Textarea label="Descrição" autosize minRows={2} value={description}
            onChange={(e) => setDescription(e.currentTarget.value)} />
          <Text size="xs" c="dimmed">Os valores anteriores ficam registados na auditoria do processo.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(false)}>Cancelar</Button>
            <Button onClick={save} loading={saving} disabled={!name.trim()}>Guardar</Button>
          </Group>
        </Stack>
      </Modal>
      <Modal opened={auditOpen} onClose={() => setAuditOpen(false)} title="Auditoria do processo" size="90%">
        <AuditPanel caseId={caseId} events={audit} />
      </Modal>
      <Modal opened={teamOpen} onClose={() => setTeamOpen(false)} title="Equipa do processo" size="lg">
        <MembersPanel caseId={caseId} members={members} canManage={canManage} onChange={reloadMembers} />
      </Modal>
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="dashboard" leftSection={<IconLayoutDashboard size={16} />}>Resumo</Tabs.Tab>
          <Tabs.Tab value="references" leftSection={<IconUsers size={16} />}>
            <Group gap={6}>1 · Pessoas <Badge size="sm" variant="light">{persons.length || references.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="videos" leftSection={<IconMovie size={16} />}>
            <Group gap={6}>2 · Vídeos <Badge size="sm" variant="light">{videos.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="jobs" leftSection={<IconActivity size={16} />}>
            <Group gap={6}>3 · Análises <Badge size="sm" variant="light">{jobs.length}</Badge></Group>
          </Tabs.Tab>
          <Tabs.Tab value="results" leftSection={<IconPhotoCheck size={16} />}>4 · Resultados</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="dashboard" pt="md">
          <CaseDashboard caseId={caseId} jobs={jobs} onOpenTab={openTab} canManage={canManage}
            photos={references.filter((r) => r.valid).length} videos={videos.length} />
        </Tabs.Panel>
        <Tabs.Panel value="references" pt="md">
          <ReferencesPanel caseId={caseId} references={references} persons={persons} onChange={reload}
            canManage={canManage} />
        </Tabs.Panel>
        <Tabs.Panel value="videos" pt="md">
          <VideosPanel caseId={caseId} videos={videos} locations={locations} onChange={reloadVideos} jobs={jobs} />
        </Tabs.Panel>
        <Tabs.Panel value="jobs" pt="md">
          <JobsPanel caseId={caseId} jobs={jobs} videos={videos} locations={locations} references={references} persons={persons}
            onChange={reloadJobs} />
        </Tabs.Panel>
        <Tabs.Panel value="results" pt="md">
          <Stack>
            <SegmentedControl w="fit-content" value={resultsView} onChange={(v) => setResultsView(v as "images" | "route")}
              data={[{ value: "images", label: "Imagens validadas" }, { value: "route", label: "Percurso" }]} />
            {resultsView === "images"
              ? <ValidatedFramesPanel caseId={caseId} jobs={jobs} locations={locations} onOpenTab={openTab} />
              : <RouteTimeline caseId={caseId} jobs={jobs} videos={videos} locations={locations} />}
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

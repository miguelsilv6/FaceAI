import { Anchor, Badge, Breadcrumbs, Group, Stack, Tabs, Text, Title } from "@mantine/core";
import { IconActivity, IconHistory, IconMovie, IconPhoto } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, formatDate } from "../api";
import AuditPanel from "../components/AuditPanel";
import JobsPanel from "../components/JobsPanel";
import ReferencesPanel from "../components/ReferencesPanel";
import VideosPanel from "../components/VideosPanel";
import { notifyError } from "../notify";
import type { AuditEvent, Case, Job, ReferenceImage, Video } from "../types";

export default function CaseView() {
  const caseId = Number(useParams().caseId);
  const [kase, setCase] = useState<Case | null>(null);
  const [references, setReferences] = useState<ReferenceImage[]>([]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [tab, setTab] = useState<string | null>("references");

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
  const reloadJobs = useCallback(() => {
    api.jobs(caseId).then(setJobs).catch(() => undefined);
  }, [caseId]);

  useEffect(reload, [reload]);
  useEffect(() => {
    if (tab === "audit") api.audit(caseId).then(setAudit).catch(notifyError);
  }, [tab, caseId]);

  if (!kase) return null;
  return (
    <Stack maw={1300} mx="auto">
      <Breadcrumbs>
        <Anchor component={Link} to="/">Casos</Anchor>
        <Text>#{kase.id} {kase.name}</Text>
      </Breadcrumbs>
      <div>
        <Title order={2}>{kase.name}</Title>
        <Text c="dimmed" size="sm">{kase.description || "Sem descrição"} · criado em {formatDate(kase.created_at)}
          {kase.analyst ? ` por ${kase.analyst}` : ""}</Text>
      </div>
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
          <VideosPanel caseId={caseId} videos={videos} onChange={reload} />
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

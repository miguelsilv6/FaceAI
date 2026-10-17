import { Anchor, Breadcrumbs, Button, Group, Loader, Stack, Text } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { useAuth } from "../auth";
import LocationCameras from "../components/LocationCameras";
import { notifyError } from "../notify";
import type { Case, CaseMember, Job, Location, Video } from "../types";

/** Página de um local do caso: importação dos vídeos e gestão das câmaras desse local. */
export default function LocationView() {
  const params = useParams();
  const caseId = Number(params.caseId);
  const locationId = Number(params.locationId);
  const navigate = useNavigate();
  const { user, isAdmin } = useAuth();
  const [kase, setCase] = useState<Case | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [members, setMembers] = useState<CaseMember[]>([]);
  const [loaded, setLoaded] = useState(false);
  const back = `/cases/${caseId}?tab=videos`;

  useEffect(() => {
    api.case(caseId).then(setCase).catch((e) => { notifyError(e); navigate("/"); });
    api.members(caseId).then(setMembers).catch(() => setMembers([]));
  }, [caseId, navigate]);
  const reload = useCallback(() => {
    Promise.all([api.locations(caseId), api.videos(caseId), api.jobs(caseId)])
      .then(([l, v, j]) => { setLocations(l); setVideos(v); setJobs(j); setLoaded(true); })
      .catch(notifyError);
  }, [caseId]);
  useEffect(reload, [reload]);

  const canManage = isAdmin || members.some((m) => m.user_id === user.id && m.role === "owner");
  const location = locations.find((l) => l.id === locationId);
  if (!kase || !loaded) return <Loader size="sm" />;
  return (
    <Stack maw={1300} mx="auto">
      <Breadcrumbs>
        <Anchor component={Link} to="/">Processos</Anchor>
        <Anchor component={Link} to={back}>#{kase.id} {kase.name}</Anchor>
        <Text>{location?.name ?? "Local"}</Text>
      </Breadcrumbs>
      <Group>
        <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14} />} component={Link} to={back}>
          Voltar aos locais do processo
        </Button>
      </Group>
      {location ? (
        <LocationCameras caseId={caseId} location={location} videos={videos} locations={locations} jobs={jobs}
          canManage={canManage} onChange={reload} onDeleted={() => navigate(back)} />
      ) : (
        <Text c="dimmed">Este local não existe (ou foi eliminado).</Text>
      )}
    </Stack>
  );
}

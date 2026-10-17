import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";

import { Center, Loader, MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation, useParams } from "react-router-dom";

import { AuthGate } from "./auth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";
import CaseList from "./pages/CaseList";

// Páginas carregadas a pedido: a lista de processos (página inicial) abre sem descarregar o resto da aplicação.
const Account = lazy(() => import("./pages/Account"));
const CaseView = lazy(() => import("./pages/CaseView"));
const LocationView = lazy(() => import("./pages/LocationView"));
const Results = lazy(() => import("./pages/Results"));
const Users = lazy(() => import("./pages/Users"));
const loading = <Center mih={200}><Loader size="sm" /></Center>;

/** Outra análise (ou outro instante pedido no URL) = página nova: o estado de Results é lido ao montar. */
function ResultsRoute() {
  const { jobId } = useParams();
  const { search } = useLocation();
  return <Results key={`${jobId}${search}`} />;
}

const theme = createTheme({ primaryColor: "indigo", defaultRadius: "md" });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      {/* O router envolve também as notificações: algumas têm links ("Ver resultados"). */}
      <BrowserRouter>
        <Notifications position="top-right" />
        <ErrorBoundary>
          <AuthGate>
            <Suspense fallback={loading}>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<CaseList />} />
                <Route path="cases/:caseId" element={<CaseView />} />
                <Route path="cases/:caseId/locations/:locationId" element={<LocationView />} />
                <Route path="jobs/:jobId" element={<ResultsRoute />} />
                <Route path="account" element={<Account />} />
                <Route path="users" element={<Users />} />
              </Route>
            </Routes>
            </Suspense>
          </AuthGate>
        </ErrorBoundary>
      </BrowserRouter>
    </MantineProvider>
  </StrictMode>,
);

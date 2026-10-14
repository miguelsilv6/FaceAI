import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthGate } from "./auth";
import Layout from "./components/Layout";
import Account from "./pages/Account";
import CaseList from "./pages/CaseList";
import CaseView from "./pages/CaseView";
import Results from "./pages/Results";
import Users from "./pages/Users";

const theme = createTheme({ primaryColor: "indigo", defaultRadius: "md" });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <AuthGate>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<CaseList />} />
              <Route path="cases/:caseId" element={<CaseView />} />
              <Route path="jobs/:jobId" element={<Results />} />
              <Route path="account" element={<Account />} />
              <Route path="users" element={<Users />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthGate>
    </MantineProvider>
  </StrictMode>,
);

import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import CaseList from "./pages/CaseList";
import CaseView from "./pages/CaseView";
import Results from "./pages/Results";

const theme = createTheme({ primaryColor: "indigo", defaultRadius: "md" });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<CaseList />} />
            <Route path="cases/:caseId" element={<CaseView />} />
            <Route path="jobs/:jobId" element={<Results />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </MantineProvider>
  </StrictMode>,
);

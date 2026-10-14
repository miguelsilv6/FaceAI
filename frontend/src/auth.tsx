import { Center, Loader } from "@mantine/core";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { authApi, setCsrfToken, UNAUTHORIZED_EVENT } from "./api";
import LoginFlow from "./pages/Login";
import type { AuthState, Me } from "./types";

interface AuthContextValue {
  user: Me;
  isAdmin: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fora do AuthGate");
  return ctx;
}

/** Só mostra a aplicação depois do login completo (password + MFA + mudança de password, se exigida). */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(null);
  const [loading, setLoading] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);
  const [expired, setExpired] = useState(false);

  const apply = useCallback((s: AuthState | null) => {
    setCsrfToken(s?.csrf_token ?? "");
    setState(s);
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await authApi.me());
    } catch {
      apply(null);
      try {
        setSetupRequired((await authApi.status()).setup_required);
      } catch {
        /* servidor indisponível: fica no ecrã de login */
      }
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onUnauthorized = () => {
      setExpired(true);
      apply(null);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [apply]);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setExpired(false);
      apply(null);
    }
  }, [apply]);

  if (loading) {
    return <Center h="100vh"><Loader /></Center>;
  }
  if (!state || state.stage !== "ok") {
    return (
      <LoginFlow state={state} setupRequired={setupRequired} expired={expired}
        onState={(s) => {
          setExpired(false);
          apply(s);
        }}
        onCancel={logout} />
    );
  }
  return (
    <AuthContext.Provider value={{ user: state.user, isAdmin: state.user.role === "admin", refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

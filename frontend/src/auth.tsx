import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api } from "./api";

interface AuthState {
  ready: boolean;
  authed: boolean;
  csrf: string | null;
  email: string | null;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const UNAUTH: AuthState = { ready: true, authed: false, csrf: null, email: null };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    ready: false,
    authed: false,
    csrf: null,
    email: null,
  });

  useEffect(() => {
    // Restore an existing cookie session on reload; refetch the CSRF token.
    api
      .session()
      .then((s) => setState({ ready: true, authed: true, csrf: s.csrf_token, email: s.email }))
      .catch(() => setState(UNAUTH));
  }, []);

  const login = async (email: string, password: string) => {
    const s = await api.login(email, password);
    setState({ ready: true, authed: true, csrf: s.csrf_token, email: s.email });
  };

  const logout = async () => {
    if (state.csrf) {
      try {
        await api.logout(state.csrf);
      } catch {
        // best-effort; clear local state regardless
      }
    }
    setState(UNAUTH);
  };

  return (
    <AuthContext.Provider value={{ ...state, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, type User } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/", label: "Query Chat" },
  { href: "/admin/schema", label: "Schema Manager" },
  { href: "/admin/examples", label: "Examples Manager" },
  { href: "/admin/logs", label: "Query Logs" },
  { href: "/admin/guardrails", label: "Guardrails Config" },
  { href: "/admin/model", label: "Model Config" },
  { href: "/admin/scripts", label: "Script Runner" },
];

const UserContext = createContext<User | null>(null);
export const useUser = () => useContext(UserContext);

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();

  const refresh = useCallback(async () => {
    try {
      setUser(await api.me());
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loading) return <div className="content">Loading...</div>;
  if (!user) return <LoginForm onAuthenticated={setUser} />;

  return (
    <UserContext.Provider value={user}>
      <div className="shell">
        <nav className="sidebar">
          <h1>QueryMind AI</h1>
          {NAV_ITEMS.map((item) => (
            <Link key={item.href} href={item.href} className={pathname === item.href ? "active" : ""}>
              {item.label}
            </Link>
          ))}
          <div className="spacer" />
          <div className="muted">Signed in as {user.username}</div>
          <button
            className="secondary"
            onClick={async () => {
              await api.logout();
              setUser(null);
            }}
          >
            Sign out
          </button>
        </nav>
        <main className="content">{children}</main>
      </div>
    </UserContext.Provider>
  );
}

function LoginForm({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(action: "login" | "bootstrap") {
    setBusy(true);
    setError(null);
    try {
      onAuthenticated(await api[action](username, password));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to reach the API.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="content" style={{ maxWidth: 420, margin: "80px auto" }}>
      <div className="card">
        <h2>Sign in to QueryMind AI</h2>
        <label htmlFor="username">Username</label>
        <input id="username" value={username} onChange={(event) => setUsername(event.target.value)} />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {error && <div className="error">{error}</div>}
        <div className="row" style={{ marginTop: 12 }}>
          <button disabled={busy} onClick={() => submit("login")}>
            Sign in
          </button>
          <button className="secondary" disabled={busy} onClick={() => submit("bootstrap")}>
            Create first admin
          </button>
        </div>
        <p className="muted">
          &quot;Create first admin&quot; only works once, before any account exists. Passwords need at least 8
          characters.
        </p>
      </div>
    </div>
  );
}

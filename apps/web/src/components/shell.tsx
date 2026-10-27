"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Activity, ArrowUpRight, Bell, BookOpen, Calculator, Check, ChevronDown, CircleHelp, Database, FlaskConical, LayoutDashboard, LogOut, Menu, Radio, Settings2, ShieldCheck, Target, Wallet, X, Zap } from "lucide-react";
import { api, ApiError, age, cash, setCsrf } from "@/lib/api";
import type { AlertItem, Dashboard } from "@/lib/types";
import { Logo, Spinner } from "./ui";

type Context = { data: Dashboard; refresh: () => Promise<void>; notify: (text: string) => void };
const TerminalContext = createContext<Context | null>(null);
export function useTerminal() { const c = useContext(TerminalContext); if (!c) throw new Error("Terminal context missing"); return c; }

const navigation = [
  { href: "/", label: "Opportunities", icon: LayoutDashboard },
  { href: "/best-bets", label: "Best bets", icon: Target },
  { href: "/bets", label: "Bet tracker", icon: Wallet },
  { href: "/calculators", label: "Calculators", icon: Calculator },
  { href: "/sources", label: "Data sources", icon: Database },
  { href: "/settings", label: "Settings", icon: Settings2 }
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter(), path = usePathname();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [time, setTime] = useState("");
  const [menu, setMenu] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [user, setUser] = useState("");
  const refresh = useCallback(async () => {
    try { const d = await api<Dashboard>("/dashboard"); setData(d); setError(""); }
    catch (e) { if (e instanceof ApiError && e.status === 401) router.replace("/login"); else setError((e as Error).message); }
  }, [router]);
  useEffect(() => {
    let alive = true;
    api<{ user: string; csrf: string }>("/auth/session").then(async session => { if (!alive) return; setCsrf(session.csrf); setUser(session.user); await refresh(); }).catch(e => {
      if (e instanceof ApiError && e.status === 401) router.replace("/login"); else setError("API unavailable. Start the backend and retry.");
    });
    return () => { alive = false; };
  }, [refresh, router]);
  useEffect(() => { if (!user) return; const t = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 5000); return () => clearInterval(t); }, [user, refresh]);
  useEffect(() => {
    if (!user || !data?.streaming) return;
    const stream = new EventSource("/api/stream");
    let refreshed = 0;
    const onQuote = () => {
      if (document.visibilityState === "visible" && Date.now()-refreshed >= 500) {
        refreshed = Date.now();
        void refresh();
      }
    };
    stream.addEventListener("quotes", onQuote);
    return () => { stream.removeEventListener("quotes", onQuote); stream.close(); };
  }, [user, data?.streaming, refresh]);
  useEffect(() => { const update = () => setTime(new Date().toLocaleTimeString("en-GB", { timeZone: "UTC" })); update(); const t = setInterval(update, 1000); return () => clearInterval(t); }, []);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(""), 4000); return () => clearTimeout(t); }, [toast]);
  useEffect(() => { setMenu(false); }, [path]);
  useEffect(() => { if (!data) return; void api<AlertItem[]>("/alerts").then(setAlerts).catch(() => {}); }, [data?.as_of]);
  const active = navigation.find(n => n.href === path)?.label || "Workspace";
  const unread = alerts.filter(a => !a.read).length;
  if (!data) return <div className="boot"><Logo /><div>{error ? <><p>{error}</p><button className="button primary" onClick={() => window.location.reload()}>Retry connection</button></> : <><Spinner /><span>Opening your workspace</span></>}</div></div>;
  return <TerminalContext.Provider value={{ data, refresh, notify: setToast }}>
    <div className="app-shell">
      {menu && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMenu(false)} />}
      <aside className={`sidebar ${menu ? "sidebar-open" : ""}`}>
        <Link href="/" className="brand-link" aria-label="EVHunter home"><Logo /></Link>
        <div className="workspace-switch"><span className="workspace-icon"><Target size={15} /></span><span>Personal workspace<small>Single-owner terminal</small></span><ChevronDown size={13} /></div>
        <div className="nav-caption">WORKSPACE</div>
        <nav>{navigation.map(n => <Link key={n.href} href={n.href} className={`nav-link ${path === n.href ? "active" : ""}`}><n.icon size={17} strokeWidth={1.6} /><span>{n.label}</span>{n.href === "/" && <span className="nav-count">{data.opportunities.length}</span>}</Link>)}</nav>
        <div className="sidebar-note"><div className="note-top"><Zap size={14} /><span>Every edge starts with data.</span></div><p>Compare the price. Check the rules. Size with discipline.</p><Link href="/calculators">Open calculators <ArrowUpRight size={13} /></Link></div>
        <div className="sidebar-bottom"><div className="connection"><span className={`status-dot ${error ? "warning" : ""}`} />{error ? "Connection interrupted" : data.mode === "demo" ? "Demo engine running" : "Data engine"}<span className="mono">v0.1</span></div>
          <div className="user-card"><div className="avatar">{user.slice(0, 2).toUpperCase()}</div><div><strong>{user || "Owner"}</strong><small>Workspace owner</small></div><button aria-label="Sign out" className="icon-button" onClick={async () => { await api("/auth/logout", { method: "POST" }); router.replace("/login"); }}><LogOut size={16} /></button></div>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMenu(true)}><Menu size={20} /></button><span>Workspace</span><span className="breadcrumb-slash">/</span><strong>{active}</strong></div><div className="topbar-actions"><span className="clock mono">{time || "--:--:--"}<span>UTC</span></span><span className={`environment-badge ${data.mode}`}><span className="status-dot" />{data.mode === "demo" ? "DEMO" : "LIVE FEED"}</span><div className="alert-container"><button className={`icon-button notification-button ${alertsOpen ? "selected" : ""}`} aria-label="View alerts" aria-expanded={alertsOpen} onClick={() => setAlertsOpen(v => !v)}><Bell size={17} />{unread > 0 && <i />}</button>{alertsOpen && <div className="alerts-popover"><div className="popover-title"><strong>Opportunity alerts <span>{unread}</span></strong><button className="text-button" onClick={async () => { await api("/alerts/read", { method: "POST" }); setAlerts(a => a.map(x => ({ ...x, read: true }))); }}>Mark read</button></div>{alerts.length ? alerts.slice(0, 8).map(a => <div className={`alert-item ${a.read ? "" : "unread"}`} key={a.id}><div><span className="edge-text">+{a.edge.toFixed(2)}%</span><small>{age(a.created_at)} ago</small></div><strong>{a.event}</strong><p>{a.selection}</p><small>{data.mode === "demo" ? "Synthetic alert" : a.delivery.replace("_", " ")}</small></div>) : <div className="alerts-empty">Alerts appear when an edge crosses your threshold.</div>}</div>}</div><div className="avatar top-avatar">{user.slice(0, 2).toUpperCase()}</div></div></header>
        {error && <div className="connection-banner"><Radio size={14} />{error} Displayed quotes expire automatically.</div>}
        <div className="page-content">{children}</div>
        <footer className="app-footer"><span><ShieldCheck size={13} />Private workspace<span className="footer-divider">·</span>Read-only market access</span><span>{data.counts.quote_count.toLocaleString()} quotes<span className="footer-divider">·</span>Source timestamps enforced</span></footer>
      </main>
      {toast && <div className="toast" role="status"><Check size={16} />{toast}<button onClick={() => setToast("")} aria-label="Dismiss notification"><X size={14} /></button></div>}
    </div>
  </TerminalContext.Provider>;
}

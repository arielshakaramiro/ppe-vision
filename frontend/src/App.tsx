import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import type { ElementType } from "react";
import { api, type Me, type Role } from "./api";
import Live from "./features/live";
import Login from "./features/auth";
import { StatusBar } from "./components";
import { SessionContext } from "./session";

const Agent = lazy(() => import("./features/agent"));
const Analytics = lazy(() => import("./features/analytics"));
const Audit = lazy(() => import("./features/admin/Audit"));
const Business = lazy(() => import("./features/admin/Business"));
const Evidence = lazy(() => import("./features/evidence"));
const Interventions = lazy(() => import("./features/interventions"));
const RecordedAnalysis = lazy(() => import("./features/recordings"));
const Settings = lazy(() => import("./features/admin/Settings"));
const System = lazy(() => import("./features/admin/System"));
const Users = lazy(() => import("./features/admin/Users"));

type TabDef = [string, string, ElementType, Role[]];

const TABS: TabDef[] = [
  ["live", "Live", Live, ["viewer", "supervisor", "admin"]],
  ["analisis-rekaman", "Analisis rekaman", RecordedAnalysis, ["admin"]],
  ["analitik", "Analitik", Analytics, ["viewer", "supervisor", "admin"]],
  ["evidence", "Evidence", Evidence, ["viewer", "supervisor", "admin"]],
  ["intervensi", "Intervensi", Interventions, ["viewer", "supervisor", "admin"]],
  ["agent", "Agent", Agent, ["viewer", "supervisor", "admin"]],
  ["sistem", "Sistem", System, ["viewer", "supervisor", "admin"]],
  ["bisnis", "Bisnis", Business, ["viewer", "supervisor", "admin"]],
  ["audit", "Audit", Audit, ["supervisor", "admin"]],
  ["pengaturan", "Pengaturan", Settings, ["admin"]],
  ["pengguna", "Pengguna", Users, ["admin"]],
];

const ROLE_LABEL: Record<Role, string> = { viewer: "Viewer", supervisor: "Supervisor", admin: "Admin" };

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<string>(location.hash.slice(1) || "live");
  const [sysStatus, setSysStatus] = useState<any>(null);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  const check = useCallback(() => {
    api.me()
      .then((m) => { setMe(m); setNotice(null); })
      .catch((e: Error) => { setMe(null); if (!e.message.startsWith("Perlu login")) setNotice(e.message); })
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (!me) return;
    const loadSys = () => api.systemStatus().then(setSysStatus).catch(() => {});
    loadSys();
    const t = setInterval(loadSys, 5000);
    return () => clearInterval(t);
  }, [me]);

  useEffect(() => {
    check();
    const onLogout = (e: Event) => {
      setMe(null);
      setNotice((e as CustomEvent<string>).detail || "Sesi berakhir. Silakan masuk lagi.");
    };
    window.addEventListener("ppe:logout", onLogout);
    return () => window.removeEventListener("ppe:logout", onLogout);
  }, [check]);

  useEffect(() => {
    if (!accountOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!accountMenuRef.current?.contains(e.target as Node)) setAccountOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAccountOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [accountOpen]);

  if (checking) return <p className="muted" style={{ padding: 24 }}>Memuat…</p>;
  if (!me) return <Login notice={notice} onLogin={(m) => { setMe(m); setNotice(null); }} />;

  const tabs = TABS.filter((t) => t[3].includes(me.role));
  const mainTabs = tabs.filter(([k]) => ["live", "analisis-rekaman", "analitik", "evidence", "intervensi", "agent"].includes(k));
  const accountTabs = tabs.filter(([k]) => !mainTabs.some(([mainKey]) => mainKey === k));
  const Page = (tabs.find((t) => t[0] === tab) ?? tabs[0])[2];
  const accountInitial = (me.username.trim().slice(0, 1) || "A").toUpperCase();
  const go = (t: string) => {
    location.hash = t;
    setTab(t);
    setAccountOpen(false);
    window.scrollTo({ top: 0, left: 0 });
  };
  const changePassword = async () => {
    const old = prompt("Password lama:");
    if (!old) return;
    const baru = prompt("Password baru (minimal 10 karakter, bukan kata umum atau username):");
    if (!baru) return;
    try {
      await api.changePassword(old, baru);
      alert("Password diganti. Silakan masuk lagi dengan password baru.");
      setMe(null);
    } catch (e) {
      alert(`Gagal: ${(e as Error).message}`);
    }
  };
  const logout = async () => {
    await api.logout().catch(() => {});
    setMe(null);
    setNotice(null);
  };

  return (
    <SessionContext.Provider value={me}>
      <div className="app">
        <div className="av-backdrop" aria-hidden="true" />
        <header className="av-header">
          <div className="brand av-brand">
            <img className="logo" src="/apd-vision/logo.svg" alt="APD Vision" />
            <b>APD Vision</b>
          </div>
          <nav className="av-nav" aria-label="Navigasi utama">
            {mainTabs.map(([k, label]) => (
              <button key={k} type="button" className={tab === k ? "on" : ""} onClick={() => go(k)}>{label}</button>
            ))}
          </nav>
          <div className="who">
            {sysStatus?.data_contoh && <span className="demo av-hide-sm">Contoh data</span>}
            <div className={`account-menu ${accountOpen ? "open" : ""}`} ref={accountMenuRef}>
              <button
                type="button"
                className="account-trigger"
                aria-label={`Menu akun ${me.username}, ${ROLE_LABEL[me.role]}`}
                aria-haspopup="menu"
                aria-expanded={accountOpen}
                onClick={() => setAccountOpen((open) => !open)}
              >
                <span className="account-avatar" aria-hidden="true">{accountInitial}</span>
                <span className="account-trigger-text av-hide-sm">
                  <b>{me.username}</b>
                  <span>{ROLE_LABEL[me.role]}</span>
                </span>
                <span className="account-chevron" aria-hidden="true">⌄</span>
              </button>
              {accountOpen && (
                <div className="account-dropdown av-pop" role="menu" aria-label="Menu akun">
                  <div className="account-profile">
                    <span className="account-avatar large" aria-hidden="true">{accountInitial}</span>
                    <div className="account-profile-text">
                      <span>Masuk sebagai</span>
                      <b>{me.username}</b>
                      <small>{ROLE_LABEL[me.role]}</small>
                    </div>
                  </div>
                  {accountTabs.length > 0 && (
                    <div className="account-menu-actions" aria-label="Halaman admin">
                      {accountTabs.map(([k, label]) => (
                        <button key={k} type="button" role="menuitem" className={`account-menu-item ${tab === k ? "on" : ""}`} onClick={() => go(k)}>
                          {label === "Audit" ? "Audit log" : label}
                        </button>
                      ))}
                    </div>
                  )}
                  {!me.lokal && (
                    <div className="account-menu-actions" aria-label="Aksi akun">
                      <button type="button" role="menuitem" className="account-menu-item" onClick={changePassword}>Ganti password</button>
                      <button type="button" role="menuitem" className="account-menu-item danger" onClick={logout}>Keluar</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </header>
        {me.lokal && (
          <div className="note small banner">
            Mode lokal tanpa akun: hanya bisa dibuka dari komputer ini.{" "}
            <button className="link small" onClick={() => go("pengguna")}>Buat akun admin</button>
          </div>
        )}
        <main>
          {tab === "live" ? <Live modelVersion={sysStatus?.versi_model} onNavigate={go} /> : (
            <Suspense fallback={<p className="muted" style={{ padding: 24 }}>Memuat halaman…</p>}>
              <Page />
            </Suspense>
          )}
        </main>
        <StatusBar sys={sysStatus} />
      </div>
    </SessionContext.Provider>
  );
}

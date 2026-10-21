import { useState } from "react";
import { api, type Me } from "../../api";

export default function Login({ onLogin, notice }: { onLogin: (me: Me) => void; notice?: string | null }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(username, password, totp || undefined));
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="av-backdrop" aria-hidden="true" />
      <form className="av-card login" onSubmit={submit}>
        <div className="login-brand">
          <img src="/apd-vision/logo.svg" alt="Logo APD Vision" width={64} height={64} />
          <div className="login-title">
            <span>APD Vision</span>
            <p>Pemantauan kepatuhan APD berbasis visi komputer</p>
          </div>
        </div>
        {notice && <p className="note small" role="status">{notice}</p>}
        <label className="av-field"><span>Nama pengguna</span>
          <input className="av-input" autoFocus required autoComplete="username" placeholder="Masukkan nama pengguna" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="av-field"><span>Kata sandi</span>
          <input className="av-input" type="password" required autoComplete="current-password" value={password}
            placeholder="Masukkan kata sandi" onChange={(e) => setPassword(e.target.value)} /></label>
        <label className="av-field"><span>Kode 2FA</span>
          <input className="av-input" inputMode="numeric" pattern="[0-9]*" maxLength={6} autoComplete="one-time-code" value={totp}
            placeholder="Opsional jika belum diaktifkan"
            onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))} /></label>
        {error && <p className="error small" role="alert">{error}</p>}
        <button className="av-btn primary login-submit" disabled={busy}>{busy ? "Memeriksa akses…" : "Masuk"}</button>
        <p className="muted small login-help">Lupa kata sandi? Minta admin untuk meresetnya.</p>
      </form>
    </div>
  );
}

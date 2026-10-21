import { useEffect, useState } from "react";
import { api, type Role, type UserRow } from "../../api";
import { useMe } from "../../session";
import { GlassSelect } from "../../ui";

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "viewer", label: "Viewer", hint: "Melihat semua halaman, bertanya ke agent, mencetak laporan" },
  { value: "supervisor", label: "Supervisor", hint: "Viewer + me-review evidence, mencatat dan menghapus intervensi" },
  { value: "admin", label: "Admin", hint: "Supervisor + mengganti sumber kamera, mengelola akun" },
];

export default function Users() {
  const me = useMe();
  const [rows, setRows] = useState<UserRow[]>([]);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role });
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = () => api.users().then(setRows).catch((e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    setInfo(null);
    try {
      await fn();
      setInfo(ok);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (me?.lokal) {
      // akun pertama mengakhiri mode lokal: langsung arahkan ke halaman login
      setError(null);
      try {
        await api.addUser(form.username, form.password, form.role);
        window.dispatchEvent(new CustomEvent("ppe:logout", {
          detail: `Akun ${form.username} dibuat. Mode lokal berakhir, silakan masuk dengan akun tersebut.`,
        }));
      } catch (err) {
        setError((err as Error).message);
      }
      return;
    }
    act(async () => {
      await api.addUser(form.username, form.password, form.role);
      setForm({ username: "", password: "", role: "viewer" });
    }, "Akun dibuat.");
  };

  const reset = (u: UserRow) => {
    const pw = prompt(`Password baru untuk ${u.username} (minimal 10 karakter):`);
    if (pw) act(() => api.updateUser(u.username, { password: pw }), `Password ${u.username} direset; sesi lamanya diakhiri.`);
  };

  return (
    <div className="stack">
      {me?.lokal && (
        <p className="note small">
          Mode lokal: belum ada akun, jadi dashboard hanya bisa dibuka dari komputer server ini. Buat akun <b>admin</b> pertama
          di bawah, lalu masuk dengan akun itu. Setelah itu perangkat lain di jaringan bisa memakai dashboard dengan login.
        </p>
      )}
      <div className="av-card">
        <h3>Tambah akun</h3>
        <form className="form-grid" onSubmit={add}>
          <label className="av-field"><span>Username (huruf kecil, angka, . _ -)</span>
            <input className="av-input" required minLength={3} maxLength={32} pattern="[a-z0-9._\-]{3,32}" autoComplete="off"
              value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} /></label>
          <label className="av-field"><span>Password (minimal 10 karakter, bukan kata umum atau username)</span>
            <input className="av-input" type="password" required minLength={10} autoComplete="new-password"
              value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
          <label className="av-field"><span>Peran</span>
            <GlassSelect
              ariaLabel="Peran akun baru"
              value={form.role}
              onChange={(v) => setForm({ ...form, role: v as Role })}
              options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
            /></label>
          <div className="av-field"><span>&nbsp;</span><button className="av-btn primary">Buat akun</button></div>
        </form>
        <ul className="plain small muted">{ROLES.map((r) => <li key={r.value}><b>{r.label}</b>: {r.hint}</li>)}</ul>
        {error && <p className="error" role="alert">{error}</p>}
        {info && <p className="good small">{info}</p>}
      </div>

      <div className="av-card">
        <h3>Daftar akun</h3>
        <div className="table-wrap">
          <table className="av-table">
            <thead><tr><th>Username</th><th>Peran</th><th>Status</th><th>ID Telegram</th><th>Login terakhir</th><th></th></tr></thead>
            <tbody>{rows.map((u) => (
              <tr key={u.username}>
                <td>{u.username}{u.username === me?.username && <span className="muted small"> (Anda)</span>}</td>
                <td>
                  <GlassSelect
                    ariaLabel={`Peran ${u.username}`}
                    value={u.role}
                    onChange={(v) => act(() => api.updateUser(u.username, { role: v as Role }), `Peran ${u.username} diubah.`)}
                    options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
                  />
                </td>
                <td><span className={`av-tag ${u.aktif ? "good" : ""}`}><i />{u.aktif ? "Aktif" : "Nonaktif"}</span></td>
                <td>
                  <input className="tg-input av-input" inputMode="numeric" pattern="[0-9]*" maxLength={20} placeholder="–"
                    defaultValue={u.telegram_id ?? ""} aria-label={`ID Telegram ${u.username}`}
                    onBlur={(e) => e.target.value !== (u.telegram_id ?? "") &&
                      act(() => api.updateUser(u.username, { telegram_id: e.target.value.trim() }), `ID Telegram ${u.username} disimpan.`)} />
                </td>
                <td className="muted small">{u.login_terakhir ? new Date(u.login_terakhir).toLocaleString("id-ID") : "–"}</td>
                <td><div className="row gap">
                  <button className="link small" onClick={() => reset(u)}>Reset password</button>
                  <button className="link small"
                    onClick={() => act(() => api.updateUser(u.username, { active: !u.aktif }), u.aktif ? `${u.username} dinonaktifkan.` : `${u.username} diaktifkan.`)}>
                    {u.aktif ? "Nonaktifkan" : "Aktifkan"}
                  </button>
                </div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <p className="muted small">Mengubah peran, menonaktifkan, atau mereset password langsung mengakhiri sesi login akun tersebut.</p>
        <p className="muted small">
          ID Telegram (angka, bisa dilihat lewat bot seperti @userinfobot) menghubungkan tombol review di alert Telegram ke akun ini.
          Setelah ada satu akun terhubung, hanya akun supervisor/admin yang terhubung yang bisa me-review dari Telegram.
        </p>
      </div>
    </div>
  );
}

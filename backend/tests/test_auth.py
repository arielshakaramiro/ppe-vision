"""Akun, sesi, peran, CSRF, dan pembatas login gagal."""
import hashlib
import io
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import auth
from app.db import AuditLog, Intervention, SessionToken, User, Violation, engine, init_db
from app.main import app

init_db()
PW = "rahasia-panjang-1"


@pytest.fixture(autouse=True)
def accounts():
    """Buat tiga akun; dihapus lagi supaya test lain tetap berjalan di mode lokal."""
    auth.throttle.clear()
    with Session(engine) as db:
        for t in (SessionToken, User, Violation, Intervention, AuditLog):
            db.exec(delete(t))
        db.commit()
        for name, role in (("vina", "viewer"), ("sapto", "supervisor"), ("adi", "admin")):
            auth.create_user(db, name, PW, role)
        v = Violation(session="s", camera_id="C", track_id=1, started_at=datetime(2026, 10, 1, 8), missing_items="helmet")
        db.add(v)
        db.commit()
    yield
    with Session(engine) as db:
        for t in (SessionToken, User, Violation, Intervention, AuditLog):
            db.exec(delete(t))
        db.commit()
    auth.throttle.clear()


def login(name: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _vid() -> int:
    with Session(engine) as db:
        return db.exec(select(Violation)).first().id


def test_everything_requires_login_once_accounts_exist():
    anon = TestClient(app)
    for path in ("/api/stats", "/api/violations", "/api/live", "/api/reports/k3", "/evidence/x.jpg", "/api/users"):
        assert anon.get(path).status_code == 401, path
    assert anon.get("/api/auth/me").status_code == 401


def test_live_returns_frame_size_for_clean_frontend_overlay(monkeypatch):
    class LiveSnapshot:
        data = {
            "running": True,
            "error": None,
            "fps": 12.5,
            "frame": {"width": 1920, "height": 1080},
            "people": [],
        }

    monkeypatch.setattr("app.main.get_remote_live", lambda camera: LiveSnapshot())
    response = login("vina").get("/api/live", params={"camera": "CAM-01"})

    assert response.status_code == 200
    assert response.json()["frame"] == {"width": 1920, "height": 1080}


@pytest.mark.parametrize("who,review,interv,camera,users", [
    ("vina", 403, 403, 403, 403),
    ("sapto", 200, 201, 403, 403),
    ("adi", 200, 201, 400, 200),   # admin lolos cek peran; 400 = URL kamera ditolak validasi
])
def test_role_matrix(who, review, interv, camera, users):
    c = login(who)
    assert c.get("/api/stats").status_code == 200
    assert c.patch(f"/api/violations/{_vid()}/review", json={"review": "confirmed"}).status_code == review
    assert c.post("/api/interventions", json={"at": "2026-09-30T07:00", "title": "Briefing"}).status_code == interv
    assert c.post("/api/camera", json={"mode": "stream", "url": "rtsp://127.0.0.1/x"}).status_code == camera
    assert c.get("/api/users").status_code == users


def test_audit_records_who_did_it():
    c = login("sapto")
    c.patch(f"/api/violations/{_vid()}/review", json={"review": "false_positive"})
    with Session(engine) as db:
        log = db.exec(select(AuditLog).where(AuditLog.action == "review_false_positive")).one()
    assert log.actor == "sapto"


def test_audit_actor_options_and_linked_event():
    supervisor = login("sapto")
    vid = _vid()
    assert supervisor.patch(f"/api/violations/{vid}/review", json={"review": "confirmed"}).status_code == 200

    actors = supervisor.get("/api/audit/actors", params={"period": "30_hari"})
    assert actors.status_code == 200
    assert actors.json()["items"] == sorted(set(actors.json()["items"]))
    assert "sapto" in actors.json()["items"]

    audit_rows = supervisor.get("/api/audit", params={"period": "30_hari", "actor": "sapto"}).json()["items"]
    review_row = next(row for row in audit_rows if row["aksi"] == "review_confirmed")
    assert review_row["violation_id"] == vid

    evidence = supervisor.get(f"/api/violations/{review_row['violation_id']}")
    assert evidence.status_code == 200
    assert evidence.json()["id"] == vid
    assert evidence.json()["review"] == "confirmed"
    assert supervisor.get("/api/violations/999999").status_code == 404

    viewer = login("vina")
    assert viewer.get(f"/api/violations/{vid}").status_code == 200
    assert viewer.get("/api/audit/actors", params={"period": "30_hari"}).status_code == 403


def test_review_export_is_admin_only_and_audited(monkeypatch, tmp_path):
    evidence = b"foto-evidence-review"
    image = tmp_path / "event.jpg"
    image.write_bytes(evidence)
    with Session(engine) as db:
        row = db.exec(select(Violation)).one()
        row.review = "confirmed"
        row.model_version = "v-uji"
        row.evidence_full = image.name
        row.evidence_sha256 = hashlib.sha256(evidence).hexdigest()
        db.add(row)
        db.commit()

    import app.main as main
    monkeypatch.setattr(main, "EVIDENCE_DIR", tmp_path)
    assert login("sapto").post("/api/model-quality/export").status_code == 403

    response = login("adi").post("/api/model-quality/export", params={"version": "v-uji"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "hasil-review-apd-" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "manifest.csv" in archive.namelist()
        assert any(name.startswith("confirmed/") for name in archive.namelist())
        manifest = archive.read("manifest.csv").decode()
        assert "v-uji" in manifest and ",True" in manifest

    with Session(engine) as db:
        log = db.exec(select(AuditLog).where(AuditLog.action == "hasil_review_diekspor")).one()
    assert log.actor == "adi"
    assert "terkonfirmasi=1" in log.detail


def test_wrong_password_and_unknown_user_look_the_same():
    c = TestClient(app)
    a = c.post("/api/auth/login", json={"username": "vina", "password": "salah-salah-salah"})
    b = c.post("/api/auth/login", json={"username": "tidakada", "password": "salah-salah-salah"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_brute_force_is_throttled():
    c = TestClient(app)
    codes = [c.post("/api/auth/login", json={"username": "vina", "password": f"salah-{i}-xxxx"}).status_code
             for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    # password benar pun ditolak selama terkunci
    assert c.post("/api/auth/login", json={"username": "vina", "password": PW}).status_code == 429


def test_cookie_flags_and_token_not_stored_in_plain():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "vina", "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = c.cookies.get(auth.COOKIE)
    with Session(engine) as db:
        stored = db.exec(select(SessionToken)).all()
    assert stored and all(s.token_hash != token for s in stored)
    with Session(engine) as db:
        assert all(u.password_hash.startswith("scrypt$") and PW not in u.password_hash for u in db.exec(select(User)))


def test_logout_ends_session():
    c = login("vina")
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/stats").status_code == 401


def test_expired_session_rejected():
    c = login("vina")
    with Session(engine) as db:
        for s in db.exec(select(SessionToken)).all():
            s.expires_at = datetime.now() - timedelta(hours=1)
            db.add(s)
        db.commit()
    assert c.get("/api/stats").status_code == 401


def test_disable_and_demote_take_effect_immediately():
    viewer_client, admin_client = login("sapto"), login("adi")
    assert admin_client.patch("/api/users/sapto", json={"role": "viewer"}).status_code == 200
    assert viewer_client.get("/api/stats").status_code == 401  # sesi lama dicabut
    c = login("sapto")
    assert c.patch(f"/api/violations/{_vid()}/review", json={"review": "confirmed"}).status_code == 403
    assert admin_client.patch("/api/users/sapto", json={"active": False}).status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"username": "sapto", "password": PW}).status_code == 401


def test_last_admin_cannot_be_removed():
    a = login("adi")
    assert a.patch("/api/users/adi", json={"role": "viewer"}).status_code == 400
    assert a.patch("/api/users/adi", json={"active": False}).status_code == 400


def test_create_user_validation():
    a = login("adi")
    assert a.post("/api/users", json={"username": "budi", "password": "pendek", "role": "viewer"}).status_code == 400
    assert a.post("/api/users", json={"username": "Bu Di!", "password": PW, "role": "viewer"}).status_code in (400, 422)
    assert a.post("/api/users", json={"username": "budi", "password": PW, "role": "root"}).status_code == 422
    assert a.post("/api/users", json={"username": "budi", "password": PW, "role": "viewer"}).status_code == 201
    assert a.post("/api/users", json={"username": "budi", "password": PW, "role": "viewer"}).status_code == 400
    assert "password_hash" not in a.get("/api/users").text


def test_change_own_password_logs_out_everywhere():
    c, other_device = login("vina"), login("vina")
    r = c.post("/api/auth/password", json={"old_password": PW, "new_password": "kata-sandi-baru-99"})
    assert r.status_code == 204
    assert other_device.get("/api/stats").status_code == 401
    assert c.post("/api/auth/password", json={"old_password": "salah", "new_password": "kata-sandi-baru-77"}).status_code == 401


def test_cross_site_requests_blocked():
    c = login("sapto")
    r = c.patch(f"/api/violations/{_vid()}/review", json={"review": "confirmed"},
                headers={"Origin": "https://situs-jahat.example"})
    assert r.status_code == 403
    ok = c.patch(f"/api/violations/{_vid()}/review", json={"review": "confirmed"},
                 headers={"Origin": "http://testserver"})
    assert ok.status_code == 200


def test_frontend_files_load_without_login():
    # halaman login harus bisa tampil; API tetap terkunci
    assert TestClient(app).get("/").status_code in (200, 404)


def test_telegram_review_follows_dashboard_roles(monkeypatch):
    import app.config as config_mod
    from app.telegram_bot import handle_callback
    monkeypatch.setattr(config_mod, "TELEGRAM_CHAT_ID", "-1")
    monkeypatch.setattr(config_mod, "TELEGRAM_REVIEWER_IDS", "")
    monkeypatch.setattr("app.telegram_bot.telegram_call", lambda *a, **k: None)
    a = login("adi")
    assert a.patch("/api/users/sapto", json={"telegram_id": "555"}).status_code == 200
    assert a.patch("/api/users/vina", json={"telegram_id": "555"}).status_code == 400   # sudah dipakai
    assert a.patch("/api/users/vina", json={"telegram_id": "777"}).status_code == 200
    assert a.patch("/api/users/vina", json={"telegram_id": "abc"}).status_code == 422

    def cb(uid):
        return {"id": "1", "data": f"rv:{_vid()}:c", "from": {"id": uid, "username": "tg"},
                "message": {"message_id": 1, "chat": {"id": -1}}}

    assert "tidak terdaftar" in handle_callback(cb(777))   # viewer terhubung: ditolak
    assert "tidak terdaftar" in handle_callback(cb(999))   # tidak terhubung, sudah ada tautan lain: ditolak
    assert "Tersimpan" in handle_callback(cb(555))         # supervisor terhubung
    with Session(engine) as db:
        log = db.exec(select(AuditLog).where(AuditLog.action == "review_confirmed")).one()
    assert log.actor == "sapto"


def test_lock_is_per_device_not_per_account():
    """Lima kali salah dari satu perangkat tidak boleh mengunci pemilik akun di perangkat lain."""
    for _ in range(5):
        auth.throttle.fail("10.0.0.9", "adi")
    assert auth.throttle.blocked("10.0.0.9", "adi")
    assert not auth.throttle.blocked("10.0.0.7", "adi")


def test_one_device_spraying_many_usernames_is_blocked():
    for i in range(20):
        auth.throttle.fail("10.0.0.5", f"user{i}")
    assert auth.throttle.blocked("10.0.0.5", "siapa-saja")


def test_throttle_survives_restart():
    for _ in range(5):
        auth.throttle.fail("10.0.0.3", "vina")
    assert auth.LoginThrottle().blocked("10.0.0.3", "vina")   # instance baru = setelah server restart


@pytest.mark.parametrize("pw", ["password12345", "Admin1234567", "1234567890", "aaaaaaaaaaaa", "vina-rahasia-9", "qwerty2026!!"])
def test_common_passwords_rejected(pw):
    assert auth.password_problem("vina", pw)


def test_reasonable_password_accepted():
    assert auth.password_problem("vina", "kopi-hitam-pagi-7") is None


def test_forwarded_for_only_trusted_from_proxy():
    from app.security import parse_networks, resolve_client
    proxy = parse_networks("127.0.0.1, 172.16.0.0/12")
    # klien biasa memalsukan header: diabaikan
    assert resolve_client("203.0.113.9", "127.0.0.1", proxy) == ("203.0.113.9", False)
    # lewat proxy tepercaya: alamat asli dipakai, dan request ditandai lewat proxy
    assert resolve_client("127.0.0.1", "198.51.100.4", proxy) == ("198.51.100.4", True)
    # rantai: ambil alamat paling kanan yang bukan proxy
    assert resolve_client("172.18.0.2", "6.6.6.6, 198.51.100.4, 172.18.0.5", proxy) == ("198.51.100.4", True)
    # tanpa daftar proxy: header tidak pernah dipercaya
    assert resolve_client("127.0.0.1", "198.51.100.4", []) == ("127.0.0.1", False)


def test_healthz_is_public_but_reveals_nothing():
    r = TestClient(app).get("/api/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert TestClient(app).get("/api/health").status_code == 401


def test_authenticated_health_includes_worker_status(monkeypatch):
    from app import worker_health
    expected = {"sehat": True, "status": "berjalan"}
    monkeypatch.setattr(worker_health, "read", lambda: expected)
    response = login("sapto").get("/api/health")
    assert response.status_code == 200
    assert response.json()["worker"] == expected


def test_report_hides_logins_and_account_changes():
    c = login("adi")
    c.post("/api/auth/login", json={"username": "vina", "password": "salah-salah-salah"})
    html = login("vina").get("/api/reports/k3?period=hari_ini").text
    assert "login_gagal" not in html and "user_dibuat" not in html


def test_forwarded_request_never_gets_local_mode():
    with Session(engine) as db:
        db.exec(delete(SessionToken))
        db.exec(delete(User))
        db.commit()
    c = TestClient(app)
    assert c.get("/api/auth/me").status_code == 200                                  # langsung di server: mode lokal
    assert c.get("/api/stats", headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 403


def test_password_change_is_throttled():
    c = login("vina")
    codes = [c.post("/api/auth/password", json={"old_password": f"salah-{i}-xxxx", "new_password": "kopi-hitam-pagi-7"}).status_code
             for i in range(6)]
    assert codes[:5] == [400] * 5 and codes[5] == 429


def test_zero_limit_disables_layer(monkeypatch):
    monkeypatch.setitem(auth.throttle.limits, "user", 0)
    for _ in range(3):
        auth.throttle.fail("10.0.0.1", "vina")
    assert not auth.throttle.blocked("10.0.0.2", "vina")

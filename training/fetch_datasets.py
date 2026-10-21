# pyright: reportMissingImports=false
"""Unduh dataset PPE dari Roboflow Universe (format YOLOv8) untuk train + gold.

    # API key via getpass (tidak disimpan) atau env ROBOFLOW_API_KEY
    # 1) Cari dataset PPE yang cocok (cetak spec + kelas, tidak mengunduh):
    python training/fetch_datasets.py --search "ppe gloves helmet vest shoes"
    # 2) Unduh train + gold pakai spec dari hasil search:
    python training/fetch_datasets.py \
        --train roboflow-universe/construction-ppe/3 another-ws/ppe-gloves/1 \
        --gold  some-ws/ppe-manual-test/2 \
        --out-dir datasets

Setiap spec = "workspace/project/version". Train diunduh ke datasets/train_<i>,
gold ke datasets/gold_src. Pakai test split gold-nya lewat:
    python training/make_gold_test.py --src datasets/gold_src --split test --out datasets/gold_prod

Key TIDAK ditulis ke file mana pun (pola colab_train.ipynb).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from getpass import getpass
from pathlib import Path

PPE_WANT = {"helmet", "hardhat", "vest", "gloves", "glove", "shoes", "boots"}


def _parse_spec(spec: str) -> tuple[str, str, int | None]:
    """'workspace/project/version' atau 'workspace/project' (versi=None, ambil terbaru).

    Juga terima URL penuh https://universe.roboflow.com/<ws>/<project>[/.../<ver>].
    """
    s = spec.strip()
    if "universe.roboflow.com/" in s:
        s = s.split("universe.roboflow.com/", 1)[1]
    parts = [p for p in s.strip("/").split("/") if p and p not in ("dataset", "model")]
    if len(parts) == 2:
        return parts[0], parts[1], None
    if len(parts) >= 3 and parts[2].isdigit():
        return parts[0], parts[1], int(parts[2])
    if len(parts) >= 3:  # ws/project/<non-digit...> -> abaikan ekor, versi terbaru
        return parts[0], parts[1], None
    raise SystemExit(f"spec harus 'workspace/project[/version]' atau URL Universe, dapat: {spec!r}")


def _summarize(location: str) -> None:
    data_yaml = Path(location) / "data.yaml"
    if data_yaml.exists():
        import yaml
        names = yaml.safe_load(data_yaml.read_text()).get("names")
        print(f"  kelas: {names}")


def _latest_version(project) -> int:
    """Nomor versi terbaru dari project Roboflow (fallback 1)."""
    try:
        vers = [int(str(v).rstrip("/").split("/")[-1]) for v in project.versions()]
        return max(vers) if vers else 1
    except Exception:  # noqa: BLE001 — API shape beragam; aman fallback
        return 1


def fetch(spec: str, out: str, rf) -> str:
    ws, proj, ver = _parse_spec(spec)
    project = rf.workspace(ws).project(proj)
    if ver is None:
        ver = _latest_version(project)
        print(f"[{spec}] versi tidak diberi -> pakai terbaru v{ver}")
    dataset = project.version(ver).download("yolov8", location=out)
    print(f"[{ws}/{proj}/{ver}] -> {dataset.location}")
    _summarize(dataset.location)
    return dataset.location


def _resolve_key() -> str:
    return os.environ.get("ROBOFLOW_API_KEY") or getpass("Roboflow API key (tidak disimpan): ")


def search(query: str, key: str, limit: int = 20) -> list[dict]:
    """Cari dataset publik di Roboflow Universe. Kembalikan ringkasan spec + kelas.

    Endpoint resmi: GET https://api.roboflow.com/search?api_key=...  (body JSON POST juga didukung).
    Shape response bisa berubah; parsing dibuat defensif.
    """
    url = "https://api.roboflow.com/search"
    body = json.dumps({"query": query, "limit": limit, "fields": [
        "name", "universe_stats", "id", "classes", "images", "splits"]}).encode()
    req = urllib.request.Request(f"{url}?api_key={key}", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (host tetap)
        data = json.loads(r.read().decode())

    hits = data.get("results") or data.get("hits") or data.get("data") or []
    out = []
    for h in hits:
        # id sering berbentuk "workspace/project"; version terpisah
        rid = h.get("id") or f"{h.get('workspace','?')}/{h.get('project','?')}"
        classes = h.get("classes")
        if isinstance(classes, dict):
            classes = list(classes.keys())
        ver = h.get("version") or (h.get("versions", [{}]) or [{}])[0].get("version") or 1
        out.append({
            "spec": f"{rid}/{ver}",
            "name": h.get("name", ""),
            "classes": classes or [],
            "images": h.get("images") or h.get("imagesCount"),
            "has_test": _has_test(h),
        })
    return out


def _has_test(h: dict) -> bool:
    splits = h.get("splits") or {}
    if isinstance(splits, dict):
        return bool(splits.get("test"))
    return False


def _score(item: dict) -> int:
    """Berapa banyak kelas PPE target yang ditutupi (untuk urutkan hasil)."""
    cls = {str(c).lower() for c in item["classes"]}
    return len(cls & PPE_WANT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", help="cari dataset PPE (cetak spec + kelas, tidak mengunduh)")
    ap.add_argument("--train", nargs="*", default=[], help="spec dataset train (boleh banyak)")
    ap.add_argument("--gold", help="spec dataset gold (test split-nya jadi gold beku)")
    ap.add_argument("--out-dir", default="datasets")
    a = ap.parse_args()

    if a.search:
        items = sorted(search(a.search, _resolve_key()), key=_score, reverse=True)
        if not items:
            raise SystemExit("tak ada hasil — coba query lain (mis. 'construction safety ppe').")
        print(f"{len(items)} dataset (urut cakupan kelas PPE):\n")
        for it in items:
            cov = sorted({str(c).lower() for c in it['classes']} & PPE_WANT)
            tag = " [punya test split]" if it["has_test"] else ""
            print(f"  {it['spec']}{tag}")
            print(f"    nama: {it['name']}  gambar: {it['images']}")
            print(f"    kelas PPE: {cov}  (semua: {it['classes']})\n")
        print("Pakai spec di atas untuk --train / --gold. Gold = yang 'punya test split'.")
        return

    if not a.train and not a.gold:
        raise SystemExit("beri --search, atau minimal --train / --gold")

    from roboflow import Roboflow
    rf = Roboflow(api_key=_resolve_key())

    out_dir = Path(a.out_dir)
    for i, spec in enumerate(a.train):
        fetch(spec, str(out_dir / f"train_{i}"), rf)
    if a.gold:
        fetch(a.gold, str(out_dir / "gold_src"), rf)
        print("\nLangkah berikut:")
        print(f"  python training/make_gold_test.py --src {out_dir}/gold_src --split test --out {out_dir}/gold_prod")


if __name__ == "__main__":
    main()

# pyright: reportMissingImports=false
"""Smoke test parsing hasil search fetch_datasets (mock HTTP, tanpa key nyata).

    python training/test_fetch_search.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_datasets as fd  # noqa: E402


class _FakeResp:
    def __init__(self, d):
        self._d = json.dumps(d).encode()

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main() -> None:
    mock = {"results": [
        {"id": "ws1/ppe-full", "version": 3, "name": "PPE Full",
         "classes": ["Helmet", "Vest", "Gloves", "Shoes"], "images": 1200,
         "splits": {"test": 100, "train": 900}},
        {"id": "ws2/gloves-only", "version": 1, "name": "Gloves",
         "classes": ["glove"], "images": 300, "splits": {"train": 300}},
    ]}
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _FakeResp(mock)
    try:
        res = fd.search("ppe", "DUMMY")
    finally:
        urllib.request.urlopen = orig

    assert len(res) == 2, res
    ranked = sorted(res, key=fd._score, reverse=True)
    assert ranked[0]["spec"] == "ws1/ppe-full/3", ranked[0]
    assert fd._score(ranked[0]) == 4, "4 kelas PPE tertutup"
    assert ranked[0]["has_test"] is True
    assert ranked[1]["has_test"] is False
    # shape alternatif: "hits" + classes dict
    mock2 = {"hits": [{"id": "w/p", "version": 2, "classes": {"helmet": 1, "boots": 2}}]}
    urllib.request.urlopen = lambda *a, **k: _FakeResp(mock2)
    try:
        r2 = fd.search("x", "DUMMY")
    finally:
        urllib.request.urlopen = orig
    assert r2[0]["spec"] == "w/p/2" and fd._score(r2[0]) == 2, r2

    print("OK: fetch_datasets search parsing lolos (ranking + test split + shape alternatif)")


if __name__ == "__main__":
    main()

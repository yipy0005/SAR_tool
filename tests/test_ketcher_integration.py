"""Ketcher (EPAM Systems, Apache-2.0) as the Find by structure editor.

Covers the pinned installer, the isolated /ketcher/ frame and its narrower security
policy, and that query molfiles written by Ketcher are searched as drawn.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path
from re import search

import pytest
from rdkit import Chem

from app import CONTENT_SECURITY_POLICY, KETCHER_CONTENT_SECURITY_POLICY, create_app
from substructure_search import build_query

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fetch_ketcher", ROOT / "scripts" / "fetch_ketcher.py")
fetch_ketcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_ketcher)

EDITOR_FILES = [
    "LICENSE",
    "NOTICE",
    "index.html",
    "ketcher-install.json",
    "manifest.json",
    "static/css/main.96b87be0.css",
    "static/js/127.aa15387f.chunk.js",
    "static/js/main.e47c48ad.js",
    "static/js/main.e47c48ad.js.LICENSE.txt",
]


def _release_zip(path: Path, extra=()) -> str:
    """A small stand-in for the release archive, laid out like ketcher-standalone-3.18.0.zip."""
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("index.html", '<!doctype html><script defer src="./static/js/main.e47c48ad.js"></script><div id="root"></div>')
        bundle.writestr("static/js/main.e47c48ad.js", "window.ketcher = {};")
        bundle.writestr("static/js/main.e47c48ad.js.LICENSE.txt", "bundled MIT notices")
        bundle.writestr("static/js/127.aa15387f.chunk.js", "/* chunk */")
        bundle.writestr("static/css/main.96b87be0.css", "body {}")
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("popup.html", "<html></html>")
        bundle.writestr("static/js/popup.39267369.js", "/* demo build that is not installed */")
        bundle.writestr("serve.json", "{}")
        for name, content in extra:
            bundle.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fake_install(tmp_path):
    archive = tmp_path / "ketcher.zip"
    digest = _release_zip(archive)
    target = tmp_path / "ketcher"
    fetch_ketcher.install_from_zip(archive, target, expected_sha256=digest, version="3.18.0-test")
    return target


def _app(tmp_path: Path, ketcher_dir: Path, name: str = "sar.db"):
    return create_app(
        {
            "TESTING": True,
            "SAR_ENV": "test",
            "SAR_DEMO_MODE": False,
            "SAR_AUTH_MODE": "test-only",
            "SECRET_KEY": "test-secret-that-is-long-enough-for-ketcher",
            "DATABASE_PATH": str(tmp_path / name),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "KETCHER_DIR": str(ketcher_dir),
        }
    )


def test_installer_verifies_the_checksum_and_keeps_only_the_editor(tmp_path):
    archive = tmp_path / "ketcher.zip"
    digest = _release_zip(archive)
    target = tmp_path / "vendor" / "ketcher"
    manifest = fetch_ketcher.install_from_zip(archive, target, expected_sha256=digest, version="test")
    assert sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()) == EDITOR_FILES
    assert (manifest["license"], manifest["source"]) == ("Apache-2.0", "https://github.com/epam/ketcher")
    assert json.loads((target / "ketcher-install.json").read_text())["release_sha256"] == digest
    assert "Apache License" in (target / "LICENSE").read_text()[:200]
    assert "EPAM Systems" in (target / "NOTICE").read_text()


def test_installer_refuses_tampered_or_unexpected_archives_without_touching_the_install(tmp_path):
    archive = tmp_path / "ketcher.zip"
    _release_zip(archive)
    target = tmp_path / "vendor" / "ketcher"
    target.mkdir(parents=True)
    (target / "index.html").write_text("previous install")
    with pytest.raises(fetch_ketcher.KetcherInstallError, match="SHA-256 mismatch"):
        fetch_ketcher.install_from_zip(archive, target, expected_sha256="0" * 64)
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("README.txt", "no editor here")
    with pytest.raises(fetch_ketcher.KetcherInstallError, match="does not contain"):
        fetch_ketcher.install_from_zip(empty, target, expected_sha256=hashlib.sha256(empty.read_bytes()).hexdigest())
    assert (target / "index.html").read_text() == "previous install"
    assert not [path for path in target.parent.iterdir() if path.name.startswith(".ketcher-")]
    for unsafe in ("../index.html", "/etc/passwd", "static\\js\\main.e47c48ad.js", "static/js/../../app.py", "static/js/popup.39267369.js"):
        assert not fetch_ketcher.wanted_member(unsafe), unsafe
    assert fetch_ketcher.wanted_member("static/js/main.e47c48ad.js")
    with pytest.raises(fetch_ketcher.KetcherInstallError, match="HTTPS"):
        fetch_ketcher.download("http://example.org/ketcher.zip", tmp_path / "plain.zip")


def test_pinned_release_and_licence_texts():
    assert fetch_ketcher.KETCHER_VERSION == "3.18.0"
    assert fetch_ketcher.RELEASE_URL == "https://github.com/epam/ketcher/releases/download/v3.18.0/ketcher-standalone-3.18.0.zip"
    # GitHub's published digest for that release asset.
    assert fetch_ketcher.RELEASE_SHA256 == "484e7f10a0e74808ae5f43f0e6448db6f4d884bdd2189aa271108474167a3e7e"
    licence = ROOT / "third_party" / "ketcher" / "LICENSE"
    assert hashlib.sha256(licence.read_bytes()).hexdigest() == "3d54b234fa88c11d1d96380ff5fe29f3c5d8e1078ef914dfe8185ce4b1fbeac7"
    assert "Copyright (C) 2018 EPAM Systems" in (ROOT / "third_party" / "ketcher" / "NOTICE").read_text()


def _directives(policy: str) -> dict[str, str]:
    return {part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else "" for part in (item.strip() for item in policy.split(";")) if part}


def test_ketcher_frame_gets_its_own_narrow_policy(tmp_path, fake_install):
    client = _app(tmp_path, fake_install).test_client()
    page = client.get("/ketcher/")
    assert page.status_code == 200 and "main.e47c48ad.js" in page.get_data(as_text=True)
    assert page.headers["Content-Security-Policy"] == KETCHER_CONTENT_SECURITY_POLICY
    assert page.headers["X-Frame-Options"] == "SAMEORIGIN"
    strict, frame = _directives(CONTENT_SECURITY_POLICY), _directives(KETCHER_CONTENT_SECURITY_POLICY)
    # Exactly three differences: WebAssembly, blob: workers, and same-origin framing. No 'unsafe-eval'.
    assert {key for key in frame if frame.get(key) != strict.get(key)} == {"script-src", "worker-src", "frame-ancestors"}
    assert frame["script-src"] == "'self' 'wasm-unsafe-eval'" and frame["worker-src"] == "'self' blob:" and frame["frame-ancestors"] == "'self'"
    asset = client.get("/ketcher/static/js/main.e47c48ad.js")
    assert asset.status_code == 200 and asset.mimetype == "text/javascript"
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    licence = client.get("/ketcher/LICENSE")
    assert licence.mimetype == "text/plain" and "Apache License" in licence.get_data(as_text=True)
    for path in ("/ketcher/../app.py", "/ketcher/%2e%2e/app.py", "/ketcher/static/js/missing.js"):
        assert client.get(path).status_code == 404, path
    missing = _app(tmp_path, tmp_path / "not-installed", "other.db").test_client().get("/ketcher/")
    assert missing.status_code == 404 and "pixi run fetch-ketcher" in missing.get_data(as_text=True)


def test_dialog_uses_ketcher_when_installed_and_the_simple_sketcher_otherwise(tmp_path, fake_install):
    for ketcher_dir, available, name in ((fake_install, True, "with.db"), (tmp_path / "not-installed", False, "without.db")):
        client = _app(tmp_path, ketcher_dir, name).test_client()
        project_id = client.post("/api/v1/projects", json={"name": f"Ketcher {available}"}).get_json()["project"]["id"]
        html = client.get(f"/workspace/sar?project_id={project_id}").get_data(as_text=True)
        assert ('data-ketcher-url="/ketcher/?disableMacromoleculesEditor=true"' in html) is available
        assert ("data-ketcher-frame" in html) is available
        assert ("Ketcher 3.18.0-test · EPAM Systems" in html) is available
        assert ("pixi run fetch-ketcher" in html) is (not available)
        assert "data-structure-sketcher" in html


def test_production_keeps_the_strict_policy_and_the_frame_requires_login(monkeypatch, tmp_path, fake_install):
    from werkzeug.security import generate_password_hash

    monkeypatch.setenv("SAR_ENV", "production")
    monkeypatch.setenv("SAR_DEMO_MODE", "false")
    monkeypatch.setenv("SAR_DATABASE_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("SAR_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SAR_SECRET_KEY", "production-secret-that-is-long-enough-123456")
    monkeypatch.setenv("SAR_AUTH_MODE", "local")
    monkeypatch.setenv("SAR_AUTH_EMAIL", "scientist@example.test")
    monkeypatch.setenv("SAR_AUTH_PASSWORD_HASH", generate_password_hash("correct horse battery staple"))
    monkeypatch.setenv("SAR_SECURE_COOKIES", "false")
    monkeypatch.setenv("SAR_KETCHER_DIR", str(fake_install))
    client = create_app().test_client()
    anonymous = client.get("/ketcher/")
    assert anonymous.status_code == 302 and "/login" in anonymous.headers["Location"]
    token = search(r'name="_csrf_token" value="([^"]+)"', client.get("/login").get_data(as_text=True)).group(1)
    login = client.post("/login", data={"email": "scientist@example.test", "password": "correct horse battery staple", "_csrf_token": token})
    assert login.status_code == 302
    frame = client.get("/ketcher/")
    assert frame.status_code == 200 and frame.headers["Content-Security-Policy"] == KETCHER_CONTENT_SECURITY_POLICY
    assert frame.headers["X-Frame-Options"] == "SAMEORIGIN"
    workspace = client.get("/")
    assert workspace.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "wasm" not in workspace.headers["Content-Security-Policy"] and workspace.headers["X-Frame-Options"] == "DENY"
    # The 30 MB bundle is cacheable even though production pages default to no-store.
    assert client.get("/ketcher/static/js/main.e47c48ad.js").headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert workspace.headers["Cache-Control"] == "no-store"


SERIES = {
    "POS-001": "O=C(Cc1ccccc1)Nc1ccccn1",
    "POS-002": "Cc1ccc(CC(=O)Nc2ccccn2)cc1",
    "POS-004": "O=C(Cc1ccc(F)cc1)Nc1ccccn1",
    "POS-005": "O=C(Cc1ccc(Cl)cc1)Nc1ccccn1",
    "POS-007": "O=C(Cc1ccc(Cl)cc1)Nc1cccnc1",
    "TOLUENE-F": "Cc1ccc(F)cc1",
    "BENZENE": "c1ccccc1",
    "TOLUENE": "Cc1ccccc1",
}
# Written by Ketcher 3.18.0 for an [F,Cl] atom list para to an aliphatic carbon (atom list as
# "L" + "M  ALS", single-or-aromatic bonds as type 6, Marvin SMARTS atom constraints).
KETCHER_ATOM_LIST = """
  -INDIGO-10012611442D

  8  8  0  0  0  0  0  0  0  0999 V2000
   11.7090   -7.4625    0.0000 L   0  0  0  0  0  0  0  0  0  0  0  0
   12.7090   -7.4625    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   13.2090   -8.3285    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   14.2090   -8.3285    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   14.7090   -7.4625    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   15.7090   -7.4625    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   14.2090   -6.5965    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   13.2090   -6.5965    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  6  0  0  0  0
  2  3  6  0  0  0  0
  3  4  6  0  0  0  0
  4  5  6  0  0  0  0
  5  6  1  0  0  0  0
  5  7  6  0  0  0  0
  7  8  6  0  0  0  0
  8  2  6  0  0  0  0
M  ALS   1  2 F F   Cl
M  MRV SMA   2 [#6;a]
M  MRV SMA   3 [#6;a]
M  MRV SMA   4 [#6;a]
M  MRV SMA   5 [#6;a]
M  MRV SMA   6 [#6;A]
M  MRV SMA   7 [#6;a]
M  MRV SMA   8 [#6;a]
M  END
""".replace("F F   Cl\n", "F F   Cl  \n")  # Ketcher pads the last 4-character atom-list field


def _hits(molfile: str) -> list[str]:
    query = build_query(molblock=molfile)
    return sorted(name for name, smiles in SERIES.items() if Chem.MolFromSmiles(smiles).HasSubstructMatch(query["mol"]))


def _ring_with(atom_symbol: str, extra: str = "") -> str:
    ring = [(0.0, 1.4), (1.2, 0.7), (1.2, -0.7), (0.0, -1.4), (-1.2, -0.7), (-1.2, 0.7)]
    atoms = [f"{x:>10.4f}{y:>10.4f}    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0" for x, y in ring]
    atoms.append(f"    0.0000    2.9000    0.0000 {atom_symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0")
    bonds = ["  1  2  2  0", "  2  3  1  0", "  3  4  2  0", "  4  5  1  0", "  5  6  2  0", "  6  1  1  0", "  1  7  1  0"]
    return "\n".join(["", "  -INDIGO-", "", "  7  7  0  0  0  0  0  0  0  0999 V2000", *atoms, *bonds, *([extra] if extra else []), "M  END"])


def test_ketcher_query_molfiles_are_searched_as_drawn():
    assert "#9,#17" in build_query(molblock=KETCHER_ATOM_LIST)["smarts"]
    assert _hits(KETCHER_ATOM_LIST) == ["POS-004", "POS-005", "POS-007", "TOLUENE-F"]
    # An R-group label (R# + "M  RGP") means "some substituent here": unsubstituted benzene does not match.
    rgroup = _ring_with("R#", "M  RGP  1   7   1")
    assert _hits(rgroup) == sorted(name for name in SERIES if name != "BENZENE")
    # V3000, which Ketcher writes for larger or richer drawings, is read as well.
    v3000 = "\n".join([
        "", "  -INDIGO-", "", "  0  0  0     0  0            999 V3000",
        "M  V30 BEGIN CTAB", "M  V30 COUNTS 7 7 0 0 0", "M  V30 BEGIN ATOM",
        "M  V30 1 C 0 1.4 0 0", "M  V30 2 C 1.2 0.7 0 0", "M  V30 3 C 1.2 -0.7 0 0", "M  V30 4 C 0 -1.4 0 0",
        "M  V30 5 C -1.2 -0.7 0 0", "M  V30 6 C -1.2 0.7 0 0", "M  V30 7 [F,Cl] 0 2.9 0 0", "M  V30 END ATOM",
        "M  V30 BEGIN BOND", "M  V30 1 2 1 2", "M  V30 2 1 2 3", "M  V30 3 2 3 4", "M  V30 4 1 4 5", "M  V30 5 2 5 6",
        "M  V30 6 1 6 1", "M  V30 7 1 1 7", "M  V30 END BOND", "M  V30 END CTAB", "M  END",
    ])
    assert _hits(v3000) == ["POS-004", "POS-005", "POS-007", "TOLUENE-F"]


def test_dialog_script_bridges_to_the_ketcher_frame_without_messaging_or_eval():
    source = (ROOT / "static" / "structure_search.js").read_text(encoding="utf-8")
    for marker in ('contentWindow.ketcher', 'getMolfile("v2000")', 'getMolfile("v3000")', "setMolecule", "getSmiles()", "containsReaction", "simpleCanShow", 'subscribe?.("change"'):
        assert marker in source, marker
    for forbidden in ("postMessage(", "eval(", "new Function", "http://", "https://"):
        assert forbidden not in source, forbidden

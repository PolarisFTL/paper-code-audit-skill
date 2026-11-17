"""Local standard-library checks; run from this directory with unittest."""

import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

import prepare_inputs


SCRIPT = Path(__file__).with_name("prepare_inputs.py")


class PreparationTests(unittest.TestCase):
    def setUp(self):
        # Keep fixtures in the test checkout, also on Windows sandboxed runners.
        self.temporary = tempfile.TemporaryDirectory(dir=SCRIPT.parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paper = self.root / "paper.pdf"
        self.paper.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.out = self.root / "output"

    def run_script(self, code, *extra, out=None):
        result = subprocess.run([sys.executable, str(SCRIPT), "--paper", str(self.paper),
                                 "--code", str(code), "--out", str(out or self.out),
                                 "--no-pdf-text", *extra], text=True, capture_output=True,
                                cwd=SCRIPT.parent, timeout=20)
        return result

    def zip(self, names):
        path = self.root / "code.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as archive:
                for name in names:
                    archive.writestr(name, b"hello")
        # ZipInfo normalizes Windows separators when writing. Make genuinely
        # hostile headers, including both the central and local filename.
        content = path.read_bytes()
        for name in names:
            if "\\" in name:
                content = content.replace(name.replace("\\", "/").encode(), name.encode())
        path.write_bytes(content)
        return path

    def assert_failed(self, result, contains="", failure=True):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")
        if contains:
            self.assertIn(contains.lower(), result.stderr.lower())
        if failure:
            value = json.loads((self.out / "FAILURE.json").read_text(encoding="utf-8"))
            self.assertEqual(value["status"], "failed")

    def test_directory_inventory_is_read_only_and_skips(self):
        source = self.root / "repo"
        source.mkdir()
        (source / "train.py").write_bytes(b"raise Exception('DO NOT EXECUTE')\n")
        (source / "weights.bin").write_bytes(b"0123456789abcdef")
        for name in (".git", ".venv", "venv", "node_modules", "__pycache__"):
            (source / name).mkdir()
            (source / name / "ignored").write_bytes(b"ignored")
        before = (source / "train.py").read_bytes()
        result = self.run_script(source, "--hash-max-bytes", "16")
        self.assertEqual(result.returncode, 0, result.stderr)
        inputs = json.loads((self.out / "inputs.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.out / "source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(inputs["source"]["root"], str(source.resolve()))
        self.assertEqual(inputs["inventory"]["files"], 2)
        self.assertEqual(len(manifest["excluded"]), 5)
        self.assertEqual((source / "train.py").read_bytes(), before)
        self.assertFalse((self.out / "source").exists())
        files = {item["path"]: item for item in manifest["files"]}
        self.assertEqual(files["weights.bin"]["sha256"], hashlib.sha256(b"0123456789abcdef").hexdigest())
        self.assertEqual(files["train.py"]["hash_status"], "skipped")
        self.assertIn("hash_reason", files["train.py"])
        self.assertEqual(json.loads(result.stdout)["files"], 2)

    def test_directory_links_are_not_followed(self):
        source = self.root / "repo"
        source.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("private", encoding="utf-8")
        try:
            (source / "linked_dir").symlink_to(outside, target_is_directory=True)
            (source / "linked_file").symlink_to(outside / "secret")
        except OSError as error:
            self.skipTest(f"This system cannot create test symlinks: {error}")
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.out / "source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"], [])
        self.assertEqual(len(manifest["excluded"]), 2)

    def test_zip_normal_root_and_archive_hash(self):
        archive = self.zip(["repo/main.py", "repo/sub/config.json", "repo/.git/internal"])
        before = archive.read_bytes()
        result = self.run_script(archive)
        self.assertEqual(result.returncode, 0, result.stderr)
        inputs = json.loads((self.out / "inputs.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.out / "source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(inputs["source"]["root"], str(self.out / "source" / "repo"))
        self.assertEqual(inputs["source"]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual([file["path"] for file in manifest["files"]], ["main.py", "sub/config.json"])
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse((self.out / "source" / "repo" / ".git").exists())

    def test_zip_unsafe_paths(self):
        for index, name in enumerate(("../escape", "/absolute", "C:/drive", "C:relative", "repo\\backslash", "repo/../../escape", "repo/file:stream")):
            with self.subTest(name=name):
                self.out = self.root / f"out{index}"
                self.assert_failed(self.run_script(self.zip(["good.py", name])))
                self.assertFalse((self.out / "source").exists(), "Preflight must finish before any extraction")
                self.assertFalse((self.root / "escape").exists())

    def test_zip_duplicate_case_and_ancestor_conflicts(self):
        for index, names in enumerate((("a.py", "a.py"), ("Main.py", "main.py"), ("repo/a.py", "Repo/b.py"), ("node/file.py", "node"), ("node", "node/file.py"))):
            with self.subTest(names=names):
                self.out = self.root / f"out{index}"
                self.assert_failed(self.run_script(self.zip(names)))
                self.assertFalse((self.out / "source").exists())

    def test_zip_symlink_and_special_file(self):
        for index, mode in enumerate((stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO)):
            archive = self.root / f"links{index}.zip"
            member = zipfile.ZipInfo("link")
            member.create_system = 3
            member.external_attr = (mode | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(member, b"target")
            self.out = self.root / f"out{index}"
            self.assert_failed(self.run_script(archive), "special files")

    def test_tar_traversal_links_devices_and_case(self):
        for index, kind in enumerate(("traversal", "absolute", "symlink", "hardlink", "device", "case", "duplicate")):
            self.out = self.root / f"out{index}"
            archive = self.root / f"bad{index}.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                member = tarfile.TarInfo({"traversal": "../escape", "absolute": "/escape", "case": "A.py"}.get(kind, "a.py"))
                member.size = 0
                if kind in ("symlink", "hardlink", "device"):
                    member.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "device": tarfile.CHRTYPE}[kind]
                    member.linkname = "../../escape"
                output.addfile(member, io.BytesIO())
                if kind in ("case", "duplicate"):
                    output.addfile(tarfile.TarInfo("a.py"), io.BytesIO())
            with self.subTest(kind=kind):
                self.assert_failed(self.run_script(archive))
                self.assertFalse((self.out / "source").exists())

    def test_all_supported_tar_compressions(self):
        for index, (suffix, mode) in enumerate(((".tar", "w"), (".tar.gz", "w:gz"), (".tgz", "w:gz"), (".tar.bz2", "w:bz2"), (".tar.xz", "w:xz"))):
            archive = self.root / ("repo" + suffix)
            with tarfile.open(archive, mode) as output:
                root_member = tarfile.TarInfo(".")
                root_member.type = tarfile.DIRTYPE
                output.addfile(root_member)
                member = tarfile.TarInfo("./repo/run.py")
                member.size = 4
                output.addfile(member, io.BytesIO(b"pass"))
            self.out = self.root / f"out{index}"
            with self.subTest(suffix=suffix):
                result = self.run_script(archive)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.out / "source" / "repo" / "run.py").read_bytes(), b"pass")

    def test_archive_limits_before_extraction(self):
        archive = self.zip(["a.py", "b.py"])
        for index, extra in enumerate((("--max-file-bytes", "4"), ("--max-expanded-bytes", "9"), ("--max-files", "1"))):
            self.out = self.root / f"out{index}"
            with self.subTest(extra=extra):
                self.assert_failed(self.run_script(archive, *extra), "exceeds")
                self.assertFalse((self.out / "source").exists())

    def test_output_inside_source_or_existing_is_untouched(self):
        source = self.root / "repo"
        source.mkdir()
        destination = source / "generated"
        self.assert_failed(self.run_script(source, out=destination), "inside", failure=False)
        self.assertFalse(destination.exists())
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_bytes(b"keep")
        self.assert_failed(self.run_script(source, out=existing), "new", failure=False)
        self.assertEqual(list(existing.iterdir()), [existing / "keep"])

    def test_unsupported_corrupt_and_non_pdf_inputs(self):
        unsupported = self.root / "code.7z"
        unsupported.write_bytes(b"no")
        self.assert_failed(self.run_script(unsupported), "unsupported")
        self.out = self.root / "corrupt"
        broken = self.root / "code.zip"
        broken.write_bytes(b"not a zip")
        self.assert_failed(self.run_script(broken), "zip")
        self.out = self.root / "invalidpdf"
        self.paper.write_bytes(b"not pdf")
        self.assert_failed(self.run_script(broken), "pdf header")

    def test_optional_pdf_extraction_never_imports_from_project(self):
        source = self.root / "repo"
        source.mkdir()
        marker = self.root / "executed_project_code"
        (source / "pypdf.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n", encoding="utf-8")
        self.out.mkdir()
        with mock.patch.object(sys, "path", [str(source), *sys.path]), \
                mock.patch.object(prepare_inputs.shutil, "which", return_value=None):
            result = prepare_inputs.pdf_text(self.paper, self.out, (source,), False)
        self.assertFalse(marker.exists())
        self.assertEqual(result["status"], "needs_pdf_tool")
        self.assertIn("no dependency was installed", result["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

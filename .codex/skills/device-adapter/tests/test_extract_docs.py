#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "extract_docs.py"
CONTEXT_SCRIPT = SKILL_ROOT / "scripts" / "context_to_manifest.py"


@unittest.skipUnless(
    all(shutil.which(command) for command in ("pdfinfo", "pdftotext", "pdftoppm", "tesseract")),
    "PDF/OCR command prerequisites are unavailable",
)
class ExtractDocsIntegrationTest(unittest.TestCase):
    def make_image(self, path: Path, text: str) -> None:
        image = Image.new("RGB", (1600, 500), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 54)
        draw.text((60, 180), text, fill="black", font=font)
        image.save(path)

    def test_native_text_and_scanned_pdf_use_different_extractors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "docs"
            docs.mkdir()
            image_path = docs / "source.png"
            self.make_image(image_path, "NanoRadar bitrate 500000")

            subprocess.run(
                ["tesseract", str(image_path), str(docs / "native"), "-l", "eng", "pdf"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with Image.open(image_path) as image:
                image.save(docs / "scanned.pdf", "PDF", resolution=150)
            image_path.unlink()

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "radar", "--docs-dir", "docs"],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            report = json.loads(
                (root / "ops/artifacts/docs/radar/extraction_report.json").read_text()
            )
            by_name = {Path(item["path"]).name: item for item in report["documents"]}
            self.assertEqual(by_name["native.pdf"]["extraction_method"], "native_text")
            self.assertEqual(by_name["scanned.pdf"]["extraction_method"], "ocr")
            self.assertIn("500000", (root / by_name["scanned.pdf"]["text_output"]).read_text())

            inventory = json.loads(
                (root / "ops/contexts/radar.docs_inventory.json").read_text()
            )
            self.assertEqual(inventory["summary"]["pdf_count"], 2)
            self.assertEqual(inventory["summary"]["ocr_pdf_count"], 1)

            context = root / "context.txt"
            context.write_text("目标设备资料全部位于 docs/。\n", encoding="utf-8")
            context_result = subprocess.run(
                [
                    sys.executable,
                    str(CONTEXT_SCRIPT),
                    "radar",
                    "--context-file",
                    str(context),
                ],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                context_result.returncode, 0, context_result.stdout + context_result.stderr
            )
            manifest = json.loads(
                (root / "ops/contexts/radar.manifest.json").read_text()
            )
            self.assertEqual(manifest["docs"]["root"], "docs")
            self.assertEqual(
                manifest["docs"]["extraction_report"],
                "ops/artifacts/docs/radar/extraction_report.json",
            )


if __name__ == "__main__":
    unittest.main()

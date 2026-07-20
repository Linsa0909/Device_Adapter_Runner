#!/usr/bin/env python3
"""Extract docs-first evidence, using Tesseract only when PDF text is absent."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_PDF_TOOLS = ("pdfinfo", "pdftotext")
REQUIRED_OCR_TOOLS = ("pdftoppm", "tesseract")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stage(name: str, status: str, exit_code: int | None = None) -> None:
    suffix = "" if exit_code is None else f" exit_code={exit_code}"
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def meaningful_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def pdf_pages(path: Path) -> tuple[int, str]:
    result = run(["pdfinfo", str(path)])
    if result.returncode != 0:
        return 0, result.stderr.strip() or "pdfinfo failed"
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
    return (int(match.group(1)), "") if match else (0, "pdfinfo did not report page count")


def native_page_text(path: Path, page: int) -> tuple[str, str]:
    result = run([
        "pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"
    ])
    return (result.stdout, "") if result.returncode == 0 else ("", result.stderr.strip())


def ocr_page(path: Path, page: int, languages: str, work: Path) -> tuple[str, str]:
    prefix = work / f"page-{page}"
    rendered = run([
        "pdftoppm", "-f", str(page), "-l", str(page), "-r", "220", "-png",
        "-singlefile", str(path), str(prefix),
    ])
    image = prefix.with_suffix(".png")
    if rendered.returncode != 0 or not image.exists():
        return "", rendered.stderr.strip() or "pdftoppm did not create an image"
    result = run(["tesseract", str(image), "stdout", "-l", languages, "--psm", "6"])
    return (result.stdout, "") if result.returncode == 0 else ("", result.stderr.strip())


def available_languages() -> set[str]:
    result = run(["tesseract", "--list-langs"])
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines()[1:] if line.strip()}


def output_name(path: Path, digest: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.as_posix()).strip("_")
    return f"{safe}.{digest.removeprefix('sha256:')[:12]}.txt"


def write_failure(context_id: str, message: str, details: list[str]) -> None:
    output = Path("ops/artifacts/last_failure.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "context_id": context_id,
        "stage": "stage2_docs_extract",
        "error_code": "DOC_EXTRACTION_FAILED",
        "message": message,
        "details": details,
        "next_action": (
            "Install poppler-utils, tesseract-ocr, tesseract-ocr-chi-sim, "
            "and tesseract-ocr-eng, then rerun context."
        ),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--languages", default="chi_sim+eng")
    parser.add_argument("--min-native-chars", type=int, default=20)
    args = parser.parse_args()

    stage("stage2_docs_extract", "start")
    docs_dir = Path(args.docs_dir)
    artifact_dir = Path("ops/artifacts/docs") / args.context_id
    text_dir = artifact_dir / "text"
    context_dir = Path("ops/contexts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    missing = [tool for tool in REQUIRED_PDF_TOOLS if not shutil.which(tool)]
    if missing:
        write_failure(args.context_id, "Required PDF tools are missing", missing)
        stage("stage2_docs_extract", "fail", 3)
        return 3

    files = sorted(path for path in docs_dir.rglob("*") if path.is_file()) if docs_dir.exists() else []
    inventory_files: list[dict[str, object]] = []
    documents: list[dict[str, object]] = []
    errors: list[str] = []
    ocr_languages = available_languages() if shutil.which("tesseract") else set()
    requested_languages = args.languages.split("+")

    for path in files:
        digest = sha256_file(path)
        relative = path.as_posix()
        entry: dict[str, object] = {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": digest,
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "suffix": path.suffix.lower(),
        }
        inventory_files.append(entry)
        if path.suffix.lower() != ".pdf":
            continue

        page_count, page_error = pdf_pages(path)
        document: dict[str, object] = {
            "path": relative,
            "sha256": digest,
            "page_count": page_count,
            "status": "PASS",
            "pages": [],
        }
        if page_error:
            document.update(status="FAIL", error=page_error, extraction_method="failed")
            errors.append(f"{relative}: {page_error}")
            documents.append(document)
            continue

        output = text_dir / output_name(path, digest)
        combined: list[str] = []
        methods: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="device-adapter-ocr-") as temp:
            work = Path(temp)
            for page in range(1, page_count + 1):
                text, native_error = native_page_text(path, page)
                method = "native_text"
                error = native_error
                if meaningful_chars(text) < args.min_native_chars:
                    method = "ocr"
                    missing_ocr_tools = [tool for tool in REQUIRED_OCR_TOOLS if not shutil.which(tool)]
                    missing_langs = [lang for lang in requested_languages if lang not in ocr_languages]
                    if missing_ocr_tools or missing_langs:
                        detail = "missing " + ", ".join(missing_ocr_tools + missing_langs)
                        text, error = "", detail
                    else:
                        text, error = ocr_page(path, page, args.languages, work)
                methods.add(method)
                status = "PASS" if meaningful_chars(text) > 0 and not error else "FAIL"
                if status == "FAIL":
                    errors.append(f"{relative} page {page}: {error or 'no text extracted'}")
                    document["status"] = "FAIL"
                document["pages"].append({
                    "page": page,
                    "method": method,
                    "status": status,
                    "character_count": meaningful_chars(text),
                    "error": error or None,
                })
                combined.append(f"\n===== PAGE {page} method={method} =====\n{text.rstrip()}\n")
        output.write_text("".join(combined), encoding="utf-8")
        document["text_output"] = output.as_posix()
        document["extraction_method"] = (
            next(iter(methods)) if len(methods) == 1 else "mixed_native_ocr"
        )
        documents.append(document)

    summary = {
        "file_count": len(inventory_files),
        "pdf_count": len(documents),
        "native_pdf_count": sum(d.get("extraction_method") == "native_text" for d in documents),
        "ocr_pdf_count": sum(d.get("extraction_method") == "ocr" for d in documents),
        "mixed_pdf_count": sum(d.get("extraction_method") == "mixed_native_ocr" for d in documents),
        "failed_pdf_count": sum(d.get("status") != "PASS" for d in documents),
    }
    inventory = {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "generated_by": "extract_docs.py",
        "generated_at": now_iso(),
        "docs_root": docs_dir.as_posix(),
        "summary": summary,
        "files": inventory_files,
    }
    report = {
        "schema_version": "1.0",
        "context_id": args.context_id,
        "generated_by": "extract_docs.py",
        "generated_at": now_iso(),
        "ocr_languages": args.languages,
        "native_text_threshold": args.min_native_chars,
        "status": "PASS" if not errors else "FAIL",
        "documents": documents,
        "errors": errors,
        "agent_instructions": (
            "Read text_output files with PAGE markers. Treat OCR text as candidate evidence; "
            "verify protocol bytes, units, scaling, and identifiers against the rendered source page."
        ),
    }
    inventory_path = context_dir / f"{args.context_id}.docs_inventory.json"
    report_path = artifact_dir / "extraction_report.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"docs_inventory: {inventory_path}")
    print(f"extraction_report: {report_path}")
    print(f"pdf_count: {summary['pdf_count']} ocr_pdf_count: {summary['ocr_pdf_count']}")
    if errors:
        write_failure(args.context_id, "One or more PDF pages could not be extracted", errors)
        stage("stage2_docs_extract", "fail", 4)
        return 4
    stage("stage2_docs_extract", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

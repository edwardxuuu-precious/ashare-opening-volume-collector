#!/usr/bin/env python3
"""Vendor complete Google Fonts unicode-range shards using only the stdlib.

Default: fetch current official CSS, fonts, and licenses with bounded concurrency.
--replay: use the saved CSS/manifest URLs and require the same content hashes.
--check: verify the saved assets and generated CSS without network requests.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
FONT_ROOT = ROOT / "public/fonts"
CSS_PATH = ROOT / "src/app/fonts.css"
MANIFEST_PATH = ROOT / "outputs/brand-liangxu-20261010/font-assets.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
FONT_CONFIG = (
    {
        "family": "Inter",
        "slug": "inter",
        "cssUrl": "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap",
        "licenseUrl": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/OFL.txt",
    },
    {
        "family": "Noto Sans SC",
        "slug": "noto-sans-sc",
        "cssUrl": "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@100..900&display=swap",
        "licenseUrl": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt",
    },
)
ALLOWED_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com", "raw.githubusercontent.com"}
MAX_SOURCE_BYTES = 512 * 1024
MAX_FONT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SHARDS = 160


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fetch(url: str, maximum: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"Unapproved font source: {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError("Font source redirected outside the official hosts")
        content = response.read(maximum + 1)
        if response.status != 200 or len(content) > maximum:
            raise ValueError(f"Unexpected response or oversized font source: {url}")
    return content


def validate_woff2(content: bytes) -> None:
    if len(content) < 48 or content[:4] != b"wOF2":
        raise ValueError("Font response is not a WOFF2 file")
    if struct.unpack(">I", content[8:12])[0] != len(content):
        raise ValueError("WOFF2 declared length does not match downloaded bytes")
    if struct.unpack(">H", content[12:14])[0] == 0:
        raise ValueError("WOFF2 has no font tables")


def parse_faces(css: str, family: str, slug: str) -> list[dict]:
    faces = []
    for index, block in enumerate(re.findall(r"@font-face\s*\{[^}]*\}", css)):
        properties = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", block))
        if properties.get("font-family", "").strip("'\"") != family:
            raise ValueError(f"Unexpected font family in {family} CSS")
        if properties.get("font-style") != "normal" or properties.get("font-weight") != "100 900":
            raise ValueError(f"Expected a normal variable font with weight 100 900: {family}")
        sources = re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", block)
        if len(sources) != 1 or not properties.get("unicode-range"):
            raise ValueError(f"Expected one WOFF2 source and unicode-range per face: {family}")
        source = sources[0]
        local = f"public/fonts/{slug}/{index:03d}-{sha256(source.encode())[:12]}.woff2"
        faces.append({
            "family": family,
            "fontStyle": properties["font-style"],
            "fontWeight": properties["font-weight"],
            "fontDisplay": properties["font-display"],
            "unicodeRange": properties["unicode-range"],
            "sourceUrl": source,
            "path": local,
            "publicUrl": "/" + local.removeprefix("public/"),
        })
    if not 1 <= len(faces) <= MAX_SHARDS:
        raise ValueError(f"Font face count exceeds the bounded expected range: {family}")
    if len({face["sourceUrl"] for face in faces}) != len(faces):
        raise ValueError(f"Duplicate font source URLs in {family} CSS")
    return faces


def coverage(faces: list[dict]) -> int:
    codepoints = set()
    for face in faces:
        for value in face["unicodeRange"].split(","):
            value = value.strip()
            if not re.fullmatch(r"U\+[0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)?", value):
                raise ValueError(f"Unexpected unicode-range token: {value}")
            limits = value[2:].split("-")
            start, end = int(limits[0], 16), int(limits[-1], 16)
            if start > end or end > 0x10FFFF:
                raise ValueError("Invalid Unicode range")
            codepoints.update(range(start, end + 1))
    return len(codepoints)


def local_css(sources: list[tuple[dict, bytes]], faces: list[dict]) -> bytes:
    mapping = {face["sourceUrl"]: face["publicUrl"] for face in faces}
    chunks = [
        "/* Generated by tooling/fonts/vendor_ui_fonts.py. Full official unicode-range shards; no text subsetting. */\n"
    ]
    for config, content in sources:
        css = content.decode("utf-8")
        for source, target in mapping.items():
            css = css.replace(f"url({source})", f"url('{target}')")
        if "src: url(https://" in css:
            raise ValueError("Generated font CSS still has a remote source")
        chunks.append(f"\n/* {config['family']} - SIL Open Font License 1.1. */\n{css}")
    return "".join(chunks).encode("utf-8")


def read_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def check_saved(manifest: dict) -> dict:
    for item in manifest["sources"] + manifest["assets"] + [manifest["generatedCss"]]:
        path = ROOT / item["path"]
        content = path.read_bytes()
        if len(content) != item["bytes"] or sha256(content) != item["sha256"]:
            raise ValueError(f"Saved font asset differs from its manifest: {item['path']}")
        if path.suffix == ".woff2":
            validate_woff2(content)
    sources = [(config, (ROOT / f"public/fonts/sources/{config['slug']}.css").read_bytes())
               for config in FONT_CONFIG]
    if local_css(sources, manifest["assets"]) != CSS_PATH.read_bytes():
        raise ValueError("Generated CSS no longer preserves its official source CSS")
    return {"status": "verified_offline", "fontFiles": len(manifest["assets"]),
            "fontBytes": sum(item["bytes"] for item in manifest["assets"])}


def vendor(replay: bool) -> dict:
    old = read_manifest() if replay else None
    old_sources = {item["path"]: item for item in old["sources"]} if old else {}
    old_assets = {item["sourceUrl"]: item for item in old["assets"]} if old else {}
    sources = []
    source_records = []
    staged_sources = {}
    faces = []
    for config in FONT_CONFIG:
        css_path = f"public/fonts/sources/{config['slug']}.css"
        content = (ROOT / css_path).read_bytes() if replay else fetch(config["cssUrl"], MAX_SOURCE_BYTES)
        if replay and sha256(content) != old_sources[css_path]["sha256"]:
            raise ValueError("Saved official CSS changed before replay")
        sources.append((config, content))
        staged_sources[css_path] = content
        source_records.append({"kind": "official_css", "family": config["family"],
                               "sourceUrl": config["cssUrl"], "path": css_path,
                               "bytes": len(content), "sha256": sha256(content)})
        faces.extend(parse_faces(content.decode("utf-8"), config["family"], config["slug"]))
        license_path = f"public/fonts/licenses/{config['slug']}-OFL.txt"
        license_bytes = (ROOT / license_path).read_bytes() if replay else fetch(config["licenseUrl"], MAX_SOURCE_BYTES)
        if b"SIL OPEN FONT LICENSE Version 1.1" not in license_bytes:
            raise ValueError("Font license response does not contain OFL 1.1")
        if replay and sha256(license_bytes) != old_sources[license_path]["sha256"]:
            raise ValueError("Saved font license changed before replay")
        staged_sources[license_path] = license_bytes
        source_records.append({"kind": "license", "family": config["family"],
                               "sourceUrl": config["licenseUrl"], "path": license_path,
                               "bytes": len(license_bytes), "sha256": sha256(license_bytes)})
    FONT_ROOT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".vendor-ui-", dir=FONT_ROOT))
    try:
        total = 0

        def download(face: dict) -> dict:
            content = fetch(face["sourceUrl"], MAX_FONT_BYTES)
            validate_woff2(content)
            record = {**face, "bytes": len(content), "sha256": sha256(content)}
            if replay and (record["sha256"] != old_assets[face["sourceUrl"]]["sha256"] or
                           record["bytes"] != old_assets[face["sourceUrl"]]["bytes"]):
                raise ValueError("Official font content changed during replay")
            (stage / Path(face["path"]).name).write_bytes(content)
            return record

        assets = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(download, face) for face in faces]
            try:
                for future in as_completed(futures):
                    asset = future.result()
                    total += asset["bytes"]
                    if total > MAX_TOTAL_BYTES:
                        raise ValueError("Total font bytes exceeded the bounded download budget")
                    assets.append(asset)
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        assets.sort(key=lambda item: item["path"])
        generated = local_css(sources, faces)
        families = []
        for config in FONT_CONFIG:
            subset = [face for face in assets if face["family"] == config["family"]]
            families.append({"family": config["family"], "fontWeight": "100 900",
                             "files": len(subset), "bytes": sum(face["bytes"] for face in subset),
                             "unicodeRangeCodepoints": coverage(subset), "textSubsetApplied": False})
        manifest = {"schemaVersion": 1, "recordedAt": datetime.now(timezone.utc).isoformat(),
                    "provider": "Google Fonts official CSS / fonts.gstatic.com",
                    "userAgent": USER_AGENT, "parallelDownloads": 8, "retries": 0,
                    "maxTotalFontBytes": MAX_TOTAL_BYTES, "families": families,
                    "sources": source_records, "assets": assets,
                    "generatedCss": {"path": "src/app/fonts.css", "bytes": len(generated),
                                     "sha256": sha256(generated)},
                    "reproduce": ["python3 tooling/fonts/vendor_ui_fonts.py --check",
                                  "python3 tooling/fonts/vendor_ui_fonts.py --replay"]}
        for asset in assets:
            target = ROOT / asset["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            (stage / target.name).replace(target)
        for relative, content in staged_sources.items():
            target = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        CSS_PATH.write_bytes(generated)
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        check_saved(manifest)
        return {"status": "vendored_and_verified", "fontFiles": len(assets), "fontBytes": total,
                "families": families, "manifest": str(MANIFEST_PATH), "css": str(CSS_PATH)}
    finally:
        shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify saved files without network")
    mode.add_argument("--replay", action="store_true", help="re-download pinned URLs and require saved hashes")
    args = parser.parse_args()
    result = check_saved(read_manifest()) if args.check else vendor(args.replay)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

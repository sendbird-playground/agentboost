from __future__ import annotations

import argparse
import io
import json
import math
import os
import plistlib
import shutil
import subprocess
import sys
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


APP_NAME = "AgentBoost"
SOURCE_FILE = Path("macos") / "agentboost" / "AgentBoostApp.swift"
AGENTBOOST_ICON_ROCKET_EMOJI = "🚀"
PLACEHOLDER_ICON_COLOR = (35, 100, 210, 255)
ICON_SIZES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
)


@dataclass
class MacOSAppBuildResult:
    app_path: Path
    executable_path: Path
    compiled: bool


def default_agentboost_app_path(home: Path | None = None) -> Path:
    home = Path(home) if home is not None else Path.home()
    return home / "Applications" / f"{APP_NAME}.app"


def build_agentboost_app(
    repo_root: Path,
    app_path: Path | None = None,
    *,
    compile_app: bool = True,
    portable_profile: bool = False,
    ad_hoc_sign: bool = False,
    beam_release_path: Path | None = None,
    beam_elixir_version: str = "",
    beam_otp_release: str = "",
    swiftc: str = "swiftc",
) -> MacOSAppBuildResult:
    repo_root = Path(repo_root).expanduser().resolve()
    app_path = Path(app_path or default_agentboost_app_path()).expanduser()
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    executable = macos / APP_NAME
    source = repo_root / SOURCE_FILE

    if not source.exists():
        raise FileNotFoundError(f"missing Swift source: {source}")

    if app_path.exists():
        shutil.rmtree(app_path)
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, resources / source.name)
    _write_info_plist(contents / "Info.plist", repo_root, include_repo_root=not portable_profile)
    _write_privacy_manifest(resources / "PrivacyInfo.xcprivacy")
    entitlements = resources / f"{APP_NAME}.entitlements"
    _write_entitlements(entitlements, sandboxed=portable_profile)
    _write_app_icon(resources / "AppIcon.icns")
    if beam_release_path is not None:
        _bundle_beam_release(
            Path(beam_release_path),
            resources,
            elixir_version=beam_elixir_version,
            otp_release=beam_otp_release,
        )

    compiled = False
    if compile_app:
        subprocess.run(
            [swiftc, str(source), "-O", "-o", str(executable)],
            cwd=repo_root,
            text=True,
            check=True,
        )
        compiled = True
    else:
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)

    if ad_hoc_sign:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", "--entitlements", str(entitlements), str(app_path)],
            text=True,
            check=True,
        )

    return MacOSAppBuildResult(app_path=app_path, executable_path=executable, compiled=compiled)


def build_app_main(argv: Iterable[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else None
    parser = argparse.ArgumentParser(description="Build the native AgentBoost macOS app bundle.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app-path", type=Path, default=default_agentboost_app_path())
    parser.add_argument("--no-compile", action="store_true", help="Create bundle layout without compiling Swift.")
    parser.add_argument("--portable-profile", action="store_true", help="Omit local repo-root metadata and enable sandbox entitlements for portable local builds.")
    parser.add_argument("--ad-hoc-sign", action="store_true", help="Ad-hoc sign the bundle with generated entitlements for local sandbox verification.")
    parser.add_argument("--beam-release-path", type=Path, default=_env_path("AGENTBOOST_BEAM_RELEASE_PATH"), help="Copy an existing agentboost Mix release into Contents/Resources/beam/agentboost.")
    parser.add_argument("--beam-elixir-version", default=os.environ.get("AGENTBOOST_BEAM_ELIXIR_VERSION", ""), help="Elixir version recorded in AgentBoostBeamRuntime.json.")
    parser.add_argument("--beam-otp-release", default=os.environ.get("AGENTBOOST_BEAM_OTP_RELEASE", ""), help="OTP release recorded in AgentBoostBeamRuntime.json.")
    parser.add_argument("--open", action="store_true", help="Open the app after a successful build.")
    args = parser.parse_args(raw_args)

    try:
        result = build_agentboost_app(
            args.repo_root,
            args.app_path,
            compile_app=not args.no_compile,
            portable_profile=args.portable_profile,
            ad_hoc_sign=args.ad_hoc_sign,
            beam_release_path=args.beam_release_path,
            beam_elixir_version=args.beam_elixir_version,
            beam_otp_release=args.beam_otp_release,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"agentboost-build-app: {exc}", file=sys.stderr)
        return 2

    print(f"built {result.app_path} compiled={result.compiled}")
    if args.open:
        subprocess.run(["open", str(result.app_path)], check=False)
    return 0


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _write_info_plist(path: Path, repo_root: Path, *, include_repo_root: bool = True) -> None:
    payload = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": "com.sendbirdplayground.agentboost",
        "CFBundleIconFile": "AppIcon",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    if include_repo_root:
        payload["AgentBoostRepoRoot"] = str(repo_root)
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)


def _write_privacy_manifest(path: Path) -> None:
    payload = {
        "NSPrivacyAccessedAPITypes": [],
        "NSPrivacyCollectedDataTypes": [],
        "NSPrivacyTracking": False,
        "NSPrivacyTrackingDomains": [],
    }
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)


def _write_entitlements(path: Path, *, sandboxed: bool = True) -> None:
    payload: dict[str, bool] = {
        "com.apple.security.files.user-selected.read-only": True,
    }
    if sandboxed:
        payload["com.apple.security.app-sandbox"] = True
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)


def _bundle_beam_release(
    source: Path,
    resources: Path,
    *,
    elixir_version: str,
    otp_release: str,
) -> None:
    source = source.expanduser().resolve()
    entrypoint = source / "bin" / "agentboost"
    if not entrypoint.exists():
        raise FileNotFoundError(f"missing BEAM release entrypoint: {entrypoint}")
    if not os_access_executable(entrypoint):
        raise FileNotFoundError(f"BEAM release entrypoint is not executable: {entrypoint}")

    target = resources / "beam" / "agentboost"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    manifest = {
        "runtime": "elixir_beam",
        "mix_release": "agentboost",
        "entrypoint": "beam/agentboost/bin/agentboost",
        "elixir_version": elixir_version,
        "otp_release": otp_release,
        "status_item_bridge": "native_appkit_host",
        "state_contract": "agentboost_state_v1",
        "sandbox": "app_sandbox_user_selected_read_only",
        "repo_local_helper": False,
        "prompt_content_ingestion": False,
    }
    (resources / "AgentBoostBeamRuntime.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def os_access_executable(path: Path) -> bool:
    return path.is_file() and bool(path.stat().st_mode & 0o111)


def _write_app_icon(path: Path) -> None:
    path.write_bytes(final_app_icon_bytes())


@lru_cache(maxsize=1)
def final_app_icon_bytes() -> bytes:
    chunks = [(kind, _agentboost_icon_png(size)) for kind, size in ICON_SIZES]
    total_length = 8 + sum(8 + len(png) for _, png in chunks)
    payload = bytearray(b"icns" + struct.pack(">I", total_length))
    for kind, png in chunks:
        payload.extend(kind + struct.pack(">I", 8 + len(png)) + png)
    return bytes(payload)


@lru_cache(maxsize=1)
def placeholder_app_icon_bytes() -> bytes:
    png = _solid_png(1024, 1024, PLACEHOLDER_ICON_COLOR)
    total_length = 8 + 8 + len(png)
    return b"icns" + struct.pack(">I", total_length) + b"ic10" + struct.pack(">I", 8 + len(png)) + png


def _agentboost_icon_png(size: int) -> bytes:
    emoji_icon = _agentboost_emoji_icon_png(size)
    if emoji_icon is not None:
        return emoji_icon
    return _procedural_agentboost_icon_png(size)


def rocket_emoji_reference() -> str:
    return AGENTBOOST_ICON_ROCKET_EMOJI


def _agentboost_emoji_icon_png(size: int) -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        return None

    font_path = Path("/System/Library/Fonts/Apple Color Emoji.ttc")
    if not font_path.exists():
        return None
    font_size = _emoji_font_size_for_icon(size)
    try:
        font = ImageFont.truetype(str(font_path), font_size)
    except OSError:
        return None

    background = Image.open(io.BytesIO(_procedural_agentboost_icon_png(size))).convert("RGBA")
    background = background.filter(ImageFilter.GaussianBlur(radius=max(0.25, size * 0.0025)))
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    emoji_canvas_size = max(64, font_size * 2)
    emoji_canvas = Image.new("RGBA", (emoji_canvas_size, emoji_canvas_size), (0, 0, 0, 0))
    emoji_draw = ImageDraw.Draw(emoji_canvas)
    emoji_draw.text(
        (emoji_canvas_size * 0.16, emoji_canvas_size * 0.08),
        rocket_emoji_reference(),
        font=font,
        embedded_color=True,
    )
    bbox = emoji_canvas.getbbox()
    if bbox is None:
        return None
    emoji = emoji_canvas.crop(bbox)
    target_width = max(1, int(size * 0.68))
    ratio = target_width / max(1, emoji.width)
    target_height = max(1, int(emoji.height * ratio))
    emoji = emoji.resize((target_width, target_height), Image.Resampling.LANCZOS)
    padding = max(4, size // 18)
    padded_emoji = Image.new("RGBA", (emoji.width + padding * 2, emoji.height + padding * 2), (0, 0, 0, 0))
    padded_emoji.alpha_composite(emoji, (padding, padding))
    emoji = padded_emoji

    glow = Image.new("RGBA", emoji.size, (36, 215, 255, 0))
    glow_alpha = emoji.getchannel("A").filter(ImageFilter.GaussianBlur(radius=max(1, size // 36)))
    glow.putalpha(glow_alpha.point(lambda alpha: min(170, alpha)))
    x = int(size * 0.50 - target_width * 0.49)
    y = int(size * 0.49 - target_height * 0.50)

    draw.arc(
        [int(size * 0.09), int(size * 0.26), int(size * 0.92), int(size * 0.84)],
        start=192,
        end=348,
        fill=(184, 238, 255, 130),
        width=max(1, size // 72),
    )
    overlay.alpha_composite(glow, (x, y))
    overlay.alpha_composite(emoji, (x, y))
    icon = Image.alpha_composite(background, overlay)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=max(2, int(size * 0.22)),
        fill=255,
    )
    icon.putalpha(mask)
    output = io.BytesIO()
    icon.save(output, format="PNG")
    return output.getvalue()


def _emoji_font_size_for_icon(size: int) -> int:
    available_sizes = (20, 32, 40, 48, 64, 96, 160)
    target = max(20, min(160, int(size * 0.72)))
    return min(available_sizes, key=lambda value: abs(value - target))


def _procedural_agentboost_icon_png(size: int) -> bytes:
    pixels = bytearray(size * size * 4)
    angle = -0.55
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    px = 2.0 / size

    for y in range(size):
        ny = 1.0 - ((y + 0.5) / size) * 2.0
        for x in range(size):
            nx = ((x + 0.5) / size) * 2.0 - 1.0
            index = (y * size + x) * 4

            t = (nx + 1.0) * 0.35 + (ny + 1.0) * 0.25
            glow = max(0.0, 1.0 - math.hypot(nx - 0.28, ny + 0.24) / 1.05)
            star_glow = max(0.0, 1.0 - math.hypot(nx + 0.42, ny - 0.38) / 0.52)
            base = _mix_color((9, 22, 54), (28, 115, 222), min(1.0, t))
            color = _mix_color(base, (41, 209, 215), glow * 0.38)
            color = _mix_color(color, (238, 247, 255), star_glow * 0.10)
            _set_pixel(pixels, index, (*color, 255))

            ring_x = nx * 0.92 - ny * 0.20
            ring_y = nx * 0.20 + ny * 0.92
            ring = math.sqrt((ring_x / 0.78) ** 2 + ((ring_y + 0.08) / 0.42) ** 2)
            ring_alpha = _edge_alpha(abs(ring - 1.0), 0.020, px * 1.7) * 115
            if ring_alpha > 0:
                _blend_pixel(pixels, index, (210, 246, 255, int(ring_alpha)))

            if math.hypot(nx + 0.42, ny - 0.38) < 0.055:
                _blend_pixel(pixels, index, (255, 245, 174, 185))
            if math.hypot(nx - 0.48, ny - 0.50) < 0.035:
                _blend_pixel(pixels, index, (255, 255, 255, 150))

            dx = nx - 0.10
            dy = ny + 0.04
            lx = dx * cos_a + dy * sin_a
            ly = -dx * sin_a + dy * cos_a

            flame_width = max(0.0, 0.17 * (1.0 - abs(ly + 0.55) / 0.22))
            flame_alpha = _edge_alpha(abs(lx), flame_width, px * 2.0) * _range_alpha(ly, -0.75, -0.35, px * 2.0)
            if flame_alpha > 0:
                heat = max(0.0, min(1.0, (ly + 0.75) / 0.40))
                flame = _mix_color((255, 91, 38), (255, 229, 94), heat)
                _blend_pixel(pixels, index, (*flame, int(230 * flame_alpha)))

            if _point_in_triangle(lx, ly, (-0.20, -0.30), (-0.08, -0.48), (-0.02, -0.27)):
                _blend_pixel(pixels, index, (255, 125, 67, 235))
            if _point_in_triangle(lx, ly, (0.20, -0.30), (0.08, -0.48), (0.02, -0.27)):
                _blend_pixel(pixels, index, (255, 125, 67, 235))

            body_sdf = _capsule_sdf(lx, ly, -0.35, 0.31, 0.135)
            body_alpha = max(0.0, min(1.0, 0.5 - body_sdf / (px * 2.0)))
            if body_alpha > 0:
                body_shine = max(0.0, min(1.0, (lx + 0.11) / 0.22))
                body = _mix_color((218, 232, 245), (255, 255, 255), 1.0 - body_shine * 0.45)
                _blend_pixel(pixels, index, (*body, int(245 * body_alpha)))

            if _point_in_triangle(lx, ly, (-0.125, 0.22), (0.125, 0.22), (0.0, 0.49)):
                _blend_pixel(pixels, index, (255, 104, 63, 245))

            if math.hypot(lx, ly - 0.06) < 0.062:
                _blend_pixel(pixels, index, (20, 128, 219, 255))
            if math.hypot(lx + 0.020, ly - 0.082) < 0.023:
                _blend_pixel(pixels, index, (222, 255, 255, 210))

    return _png_from_rgba(pixels, size, size)


def _solid_png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    pixels = bytearray(bytes(color) * width * height)
    return _png_from_rgba(pixels, width, height)


def _png_from_rgba(pixels: bytes | bytearray, width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw_rows = []
    stride = width * 4
    for y in range(height):
        raw_rows.append(b"\x00" + bytes(pixels[y * stride : (y + 1) * stride]))
    raw = b"".join(raw_rows)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, level=9)),
            chunk(b"IEND", b""),
        ]
    )


def _set_pixel(pixels: bytearray, index: int, color: tuple[int, int, int, int]) -> None:
    pixels[index : index + 4] = bytes(color)


def _blend_pixel(pixels: bytearray, index: int, color: tuple[int, int, int, int]) -> None:
    alpha = color[3] / 255.0
    inv = 1.0 - alpha
    pixels[index] = int(color[0] * alpha + pixels[index] * inv)
    pixels[index + 1] = int(color[1] * alpha + pixels[index + 1] * inv)
    pixels[index + 2] = int(color[2] * alpha + pixels[index + 2] * inv)
    pixels[index + 3] = 255


def _mix_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _edge_alpha(distance: float, limit: float, softness: float) -> float:
    if limit <= 0:
        return 0.0
    if distance <= limit:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (distance - limit) / max(softness, 0.0001)))


def _range_alpha(value: float, low: float, high: float, softness: float) -> float:
    if value < low or value > high:
        return 0.0
    return min((value - low) / max(softness, 0.0001), (high - value) / max(softness, 0.0001), 1.0)


def _capsule_sdf(x: float, y: float, y0: float, y1: float, radius: float) -> float:
    closest_y = max(y0, min(y1, y))
    return math.hypot(x, y - closest_y) - radius


def _point_in_triangle(
    x: float,
    y: float,
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    def sign(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]) -> float:
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    p = (x, y)
    d1 = sign(p, a, b)
    d2 = sign(p, b, c)
    d3 = sign(p, c, a)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)

#!/usr/bin/env python3
"""
Convert TwoDark.tmTheme -> TwoLight.tmTheme.

Steps:
1) Load tmTheme (plist)
2) Apply semantic changes from TwoDark.tmTheme.patch
3) Extract OneDark/OneLight hex palette mapping from one.vim (by variable name)
4) Remap all #RRGGBB colors (exact, then nearest-match fallback)
5) Write out TwoLight.tmTheme with a new identity
"""

from __future__ import annotations

import argparse
import math
import plistlib
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


HEX_COLOR_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
VIM_COLOR_DEF_RE = re.compile(
    r"""^\s*let\s+s:(?P<var>[A-Za-z0-9_]+)\s*=\s*\[\s*'(?P<hex>#[0-9A-Fa-f]{6})'\s*,\s*'[^']*'\s*\]\s*(?:"[^"]*)?\s*$"""
)


def _parse_rgb(hex_color: str) -> Tuple[int, int, int]:
    c = hex_color.lower()
    if c.startswith("#"):
        c = c[1:]
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _dist2(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


@dataclass(frozen=True)
class PaletteMap:
    dark_to_light: Dict[str, str]  # "#rrggbb" -> "#rrggbb"
    dark_colors: List[str]         # palette keys, for nearest match


def extract_onedark_onelight_map(one_vim_path: Path) -> PaletteMap:
    """
    Extract mapping from `one.vim` by parsing the `if &background ==# 'dark'` / `else` palette blocks.
    Mapping is by variable name: s:hue_2 dark hex -> s:hue_2 light hex, etc.
    """
    text = one_vim_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the palette if/else block.
    if_idx = None
    else_idx = None
    endif_idx = None
    for i, line in enumerate(lines):
        if "if &background ==# 'dark'" in line:
            if_idx = i
            break
    if if_idx is None:
        raise RuntimeError("Could not find `if &background ==# 'dark'` block in one.vim")

    for i in range(if_idx + 1, len(lines)):
        if re.match(r"^\s*else\s*$", lines[i]):
            else_idx = i
            break
    if else_idx is None:
        raise RuntimeError("Could not find `else` for palette block in one.vim")

    for i in range(else_idx + 1, len(lines)):
        if re.match(r"^\s*endif\s*$", lines[i]):
            endif_idx = i
            break
    if endif_idx is None:
        raise RuntimeError("Could not find closing `endif` for palette block in one.vim")

    dark_block = lines[if_idx + 1 : else_idx]
    light_block = lines[else_idx + 1 : endif_idx]

    def parse_block(block_lines: Iterable[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ln in block_lines:
            m = VIM_COLOR_DEF_RE.match(ln)
            if not m:
                continue
            var = m.group("var")
            hx = m.group("hex").lower()
            out[var] = hx
        return out

    dark_vars = parse_block(dark_block)
    light_vars = parse_block(light_block)

    common_vars = sorted(set(dark_vars.keys()) & set(light_vars.keys()))
    if not common_vars:
        raise RuntimeError("Found no common palette variables between dark/light blocks in one.vim")

    dark_to_light: Dict[str, str] = {}
    for var in common_vars:
        d = dark_vars[var]
        l = light_vars[var]
        dark_to_light[d] = l

    # Some distinct variables can share the same dark hex; keep first mapping.
    # If collisions exist, they should map to similar light hex anyway.
    dark_colors = sorted(set(dark_to_light.keys()))
    return PaletteMap(dark_to_light=dark_to_light, dark_colors=dark_colors)


def normalize_hex_color(value: str) -> Optional[str]:
    v = value.strip()
    if not HEX_COLOR_RE.match(v):
        return None
    if not v.startswith("#"):
        v = "#" + v
    return v.lower()


def apply_patch_semantics(theme: Dict[str, Any]) -> int:
    """
    Apply the semantic edits represented by TwoDark.tmTheme.patch.
    Returns number of edits applied.
    """
    edits = 0
    settings = theme.get("settings")
    if not isinstance(settings, list):
        raise RuntimeError("Theme plist missing top-level `settings` array")

    def maybe_update_scope(entry: Dict[str, Any], *, name: str, old_sub: str, new_scope: str) -> bool:
        if entry.get("name") != name:
            return False
        scope = entry.get("scope")
        if not isinstance(scope, str):
            return False
        if old_sub not in scope and scope != old_sub:
            # We only patch when the expected scope shape is present.
            return False
        if scope == new_scope:
            return False
        entry["scope"] = new_scope
        return True

    # 1) Classes: add entity.name
    for entry in settings:
        if not isinstance(entry, dict):
            continue
        if maybe_update_scope(
            entry,
            name="Classes",
            old_sub="support.class, entity.name.class, entity.name.type.class",
            new_scope="support.class, entity.name.class, entity.name.type.class, entity.name",
        ):
            edits += 1
            break

    # 2) Headings: exclude markdown html scope
    for entry in settings:
        if not isinstance(entry, dict):
            continue
        if maybe_update_scope(
            entry,
            name="Headings",
            old_sub="markup.heading punctuation.definition.heading, entity.name.section",
            new_scope="markup.heading punctuation.definition.heading, entity.name.section, markup.heading - text.html.markdown",
        ):
            edits += 1
            break

    # 3) Json key: update scope path
    for entry in settings:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != "Json key":
            continue
        scope = entry.get("scope")
        if not isinstance(scope, str):
            continue
        if "meta.structure.dictionary.json" not in scope:
            continue
        new_scope = scope.replace("meta.structure.dictionary.json", "meta.mapping.key.json")
        if new_scope != scope:
            entry["scope"] = new_scope
            edits += 1
            break

    return edits


@dataclass
class RemapStats:
    exact: int = 0
    nearest: int = 0
    unchanged: int = 0
    invalid: int = 0


@dataclass(frozen=True)
class RemapEvent:
    path: str  # e.g. "settings[0].settings"
    key: str   # e.g. "foreground"
    old: str
    new: str
    mode: str  # exact|nearest|unchanged


@dataclass(frozen=True)
class RuleMeta:
    name: Optional[str]
    scope: Optional[str]


def _format_path_with_meta(path: str, meta: Optional[RuleMeta]) -> str:
    """
    If this path refers to a rule's `.settings` dict (e.g. `settings[12].settings`),
    annotate it with `(name=... scope=...)` to make logs readable.
    """
    if meta is None or (not meta.name and not meta.scope):
        return path

    m = re.match(r"^(settings\[\d+\]\.settings)(.*)$", path)
    if not m:
        return path

    parts: List[str] = []
    if meta.name:
        parts.append(f"name={meta.name}")
    if meta.scope:
        parts.append(f"scope={meta.scope}")
    annotation = "(" + " ".join(parts) + ")"
    return m.group(1) + annotation + m.group(2)


def remap_color(
    color_value: str,
    palette: PaletteMap,
    *,
    nearest_threshold: float,
) -> Tuple[str, str]:
    """
    Returns (new_value, mode) where mode is one of:
    - "exact" (exact palette match)
    - "nearest" (nearest palette match within threshold)
    - "unchanged"
    - "invalid" (not a hex color)
    """
    norm = normalize_hex_color(color_value)
    if norm is None:
        return color_value, "invalid"

    # Exact match.
    mapped = palette.dark_to_light.get(norm)
    if mapped is not None:
        return mapped, "exact"

    # Nearest match (only if close enough).
    rgb = _parse_rgb(norm)
    best = None
    best_d2 = None
    for dhex in palette.dark_colors:
        d2 = _dist2(rgb, _parse_rgb(dhex))
        if best_d2 is None or d2 < best_d2:
            best_d2 = d2
            best = dhex

    if best is None or best_d2 is None:
        return norm, "unchanged"

    if math.sqrt(best_d2) <= nearest_threshold:
        return palette.dark_to_light[best], "nearest"

    return norm, "unchanged"


def remap_theme_colors(
    theme: Any,
    palette: PaletteMap,
    *,
    nearest_threshold: float,
    track_non_exact: bool,
) -> Tuple[RemapStats, List[RemapEvent]]:
    stats = RemapStats()
    events: List[RemapEvent] = []

    def walk(node: Any, path: str, rule_meta: Optional[RuleMeta]) -> Any:
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if isinstance(v, str) and k in {
                    "foreground",
                    "background",
                    "caret",
                    "invisibles",
                    "lineHighlight",
                    "selection",
                    "selectionForeground",
                    "selectionBackground",
                    "divider",
                }:
                    new_v, mode = remap_color(v, palette, nearest_threshold=nearest_threshold)
                    node[k] = new_v
                    if mode == "exact":
                        stats.exact += 1
                    elif mode == "nearest":
                        stats.nearest += 1
                    elif mode == "unchanged":
                        stats.unchanged += 1
                        if track_non_exact:
                            events.append(
                                RemapEvent(
                                    path=_format_path_with_meta(path, rule_meta),
                                    key=k,
                                    old=v,
                                    new=new_v,
                                    mode=mode,
                                )
                            )
                    else:
                        stats.invalid += 1
                    if track_non_exact and mode == "nearest":
                        events.append(
                            RemapEvent(
                                path=_format_path_with_meta(path, rule_meta),
                                key=k,
                                old=v,
                                new=new_v,
                                mode=mode,
                            )
                        )
                else:
                    node[k] = walk(v, f"{path}.{k}" if path else str(k), rule_meta)
            return node
        if isinstance(node, list):
            for i, item in enumerate(node):
                child_path = f"{path}[{i}]"
                child_meta = rule_meta
                # Special-case: the top-level `settings` array contains per-rule dicts with `name` + `scope`.
                if path == "settings" and isinstance(item, dict):
                    n = item.get("name")
                    s = item.get("scope")
                    child_meta = RuleMeta(n if isinstance(n, str) else None, s if isinstance(s, str) else None)
                node[i] = walk(item, child_path, child_meta)
            return node
        return node

    walk(theme, "", None)
    return stats, events


def update_identity(theme: Dict[str, Any], *, name: str, semantic_class: str) -> None:
    theme["name"] = name
    theme["semanticClass"] = semantic_class
    theme["uuid"] = str(uuid.uuid4())


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert TwoDark.tmTheme to TwoLight.tmTheme")
    p.add_argument("--in", dest="in_path", required=True, help="Input TwoDark.tmTheme path")
    p.add_argument("--vim", dest="vim_path", required=True, help="Input one.vim path")
    p.add_argument("--out", dest="out_path", required=True, help="Output TwoLight.tmTheme path")
    p.add_argument("--name", default="TwoLight", help="Output theme name (default: TwoLight)")
    p.add_argument("--semantic-class", default="theme.light.two_light", help="Output semanticClass")
    p.add_argument("--nearest-threshold", type=float, default=50.0, help="Nearest-match threshold in RGB distance")
    p.add_argument(
        "--log-non-exact",
        action="store_true",
        help="Log every nearest/unchanged color remap with its plist path + key + old->new",
    )
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    in_path = Path(args.in_path)
    vim_path = Path(args.vim_path)
    out_path = Path(args.out_path)

    palette = extract_onedark_onelight_map(vim_path)

    with in_path.open("rb") as f:
        theme = plistlib.load(f)
    if not isinstance(theme, dict):
        raise RuntimeError("Input tmTheme plist is not a dict")

    patch_edits = apply_patch_semantics(theme)

    stats, events = remap_theme_colors(
        theme,
        palette,
        nearest_threshold=float(args.nearest_threshold),
        track_non_exact=bool(args.log_non_exact),
    )

    update_identity(theme, name=args.name, semantic_class=args.semantic_class)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        plistlib.dump(theme, f, fmt=plistlib.FMT_XML, sort_keys=False)

    print(f"patch_edits={patch_edits}")
    print(f"colors_exact={stats.exact} colors_nearest={stats.nearest} colors_unchanged={stats.unchanged} colors_invalid={stats.invalid}")
    if args.log_non_exact:
        print("--- non_exact_details (nearest/unchanged) ---")
        for ev in events:
            print(f"{ev.mode}\t{ev.path}\t{ev.key}\t{ev.old} -> {ev.new}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BrokenPipeError:
        # Allow piping to `head` etc.
        raise SystemExit(0)


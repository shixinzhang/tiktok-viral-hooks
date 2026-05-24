"""Rebuild aggregation pages from _data/all-videos.json + on-disk file tree.

  - by-niche/{lang}/{niche}.md
  - by-pattern/{lang}/hook-{pattern}.md
  - README*.md AUTO_GENERATED_LATEST_{START,END} block (latest 10)

Metadata source: _data/all-videos.json (written by generate_breakdown.py).
We no longer parse YAML frontmatter from each markdown — those files are
content-only so GitHub doesn't render a metadata table at the top.

Safe to re-run; output is deterministic for a given on-disk state.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
BREAKDOWNS_DIR = ROOT / "breakdowns"
BY_NICHE_DIR = ROOT / "by-niche"
BY_PATTERN_DIR = ROOT / "by-pattern"
ALL_VIDEOS = ROOT / "_data" / "all-videos.json"


def _format_views(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _collect() -> list[dict]:
    """Walk breakdowns/ and join each file with _data/all-videos.json."""
    items: list[dict] = []
    if not BREAKDOWNS_DIR.exists() or not ALL_VIDEOS.exists():
        return items
    try:
        catalog = json.loads(ALL_VIDEOS.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"failed to read {ALL_VIDEOS}: {e}", file=sys.stderr)
        return items

    for md_path in BREAKDOWNS_DIR.rglob("*.md"):
        slug = md_path.stem
        meta = catalog.get(slug) or {}
        if not meta:
            print(f"skip {md_path}: slug {slug} missing from all-videos.json", file=sys.stderr)
            continue
        # Path layout: breakdowns/{lang}/{YYYY-MM}/{slug}.md
        lang = md_path.parts[-3] if len(md_path.parts) >= 3 else "en"
        items.append({
            "lang": lang,
            "slug": slug,
            "niche": meta.get("niche") or "other",
            "hook_pattern": meta.get("hook_pattern") or "unknown",
            "title": meta.get("title") or slug,
            "views": int(meta.get("view_count") or 0),
            "views_formatted": _format_views(meta.get("view_count")),
            "posted_date": str(meta.get("posted_date") or "")[:10],
            "path": md_path,
        })
    return items


def _relpath_from_index(item: dict, index_dir: Path) -> str:
    try:
        return str(item["path"].relative_to(index_dir.parent.parent)).replace("\\", "/")
    except ValueError:
        return "../../" + str(item["path"].relative_to(ROOT)).replace("\\", "/")


def _write_aggregations(env: Environment, items: list[dict], group_key: str,
                        out_root: Path, template_name: str, title_field: str,
                        path_prefix: str = "") -> None:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        grouped.setdefault((it["lang"], it[group_key]), []).append(it)

    for (lang, key), entries in grouped.items():
        if not key:
            continue
        index_dir = out_root / lang
        index_dir.mkdir(parents=True, exist_ok=True)
        out_path = index_dir / f"{path_prefix}{key}.md"
        entries.sort(key=lambda x: -int(x["views"] or 0))
        rendered = env.get_template(template_name).render(
            lang=lang,
            count=len(entries),
            **{title_field: key.replace("-", " ").title()},
            items=[
                {**e, "relpath": _relpath_from_index(e, index_dir)}
                for e in entries
            ],
        )
        out_path.write_text(rendered, encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)} ({len(entries)} items)")


_LATEST_RE = re.compile(
    r"(<!-- AUTO_GENERATED_LATEST_START -->)(.*?)(<!-- AUTO_GENERATED_LATEST_END -->)",
    re.DOTALL,
)


def _rewrite_readme(items: list[dict], readme_path: Path, lang: str) -> None:
    if not readme_path.exists():
        return
    text = readme_path.read_text(encoding="utf-8")
    lang_items = [i for i in items if i["lang"] == lang]
    # Sort by view_count DESC so the highest-performing breakdowns surface
    # first in the README "Latest" block.
    lang_items.sort(key=lambda x: -int(x["views"] or 0))
    top = lang_items[:10]
    lines = []
    for it in top:
        rel = "./" + str(it["path"].relative_to(ROOT)).replace("\\", "/")
        lines.append(f"- [{it['title']}]({rel}) — {it['views_formatted']} views · `{it['hook_pattern']}` · {it['posted_date']}")
    body = ("\n" + "\n".join(lines) + "\n") if lines else "\n_No breakdowns published yet._\n"
    new_text = _LATEST_RE.sub(lambda m: m.group(1) + body + m.group(3), text)
    if new_text != text:
        readme_path.write_text(new_text, encoding="utf-8")
        print(f"refreshed Latest block in {readme_path.name} ({len(top)} entries)")


def main() -> int:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=False,
        trim_blocks=False, lstrip_blocks=False,
    )
    items = _collect()
    print(f"scanned {len(items)} breakdown file(s)")

    _write_aggregations(env, items, "niche", BY_NICHE_DIR,
                        "niche_index.md.j2", "niche_title")
    _write_aggregations(env, items, "hook_pattern", BY_PATTERN_DIR,
                        "pattern_index.md.j2", "pattern_title",
                        path_prefix="hook-")

    _rewrite_readme(items, ROOT / "README.md", "en")
    _rewrite_readme(items, ROOT / "README.zh-CN.md", "zh-CN")
    return 0


if __name__ == "__main__":
    sys.exit(main())

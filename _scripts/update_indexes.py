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


def _slugify(s) -> str:
    """File-name-safe slug for niche / hook_pattern values."""
    if not s:
        return "unknown"
    out = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return out or "unknown"


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
        niche = meta.get("niche") or "other"
        hook_raw = (meta.get("hook_pattern") or "").strip()
        # Treat empty / "unknown" as no pattern so we don't surface a useless tag.
        if not hook_raw or hook_raw.lower() == "unknown":
            hook = None
            hook_slug = None
        else:
            hook = hook_raw
            hook_slug = _slugify(hook_raw)
        # Title: SEO title preferred, fall back to original.
        seo_title = (meta.get("title_seo") or "").strip()
        title = seo_title or meta.get("title") or slug
        items.append({
            "lang": lang,
            "slug": slug,
            "niche": niche,
            "niche_slug": _slugify(niche),
            "hook_pattern": hook,
            "hook_pattern_slug": hook_slug,
            "title": title,
            "raw_title": meta.get("title") or slug,
            "thumbnail": meta.get("thumbnail") or "",
            "video_url": meta.get("video_url") or "",
            "views": int(meta.get("view_count") or 0),
            "views_formatted": _format_views(meta.get("view_count")),
            "posted_date": str(meta.get("posted_date") or "")[:10],
            "path": md_path,
        })
    return items


def _relpath_from_index(item: dict, index_dir: Path) -> str:
    """Build a markdown link from an aggregation page (e.g. by-niche/en/foo.md)
    to a breakdown file. Both are absolute; use os.path.relpath so we get
    the proper number of '..' segments."""
    import os
    return os.path.relpath(item["path"], start=index_dir).replace("\\", "/")


def _write_aggregations(env: Environment, items: list[dict], group_key: str,
                        out_root: Path, template_name: str, title_field: str,
                        path_prefix: str = "") -> None:
    slug_key = f"{group_key}_slug"
    grouped: dict[tuple[str, str], list[dict]] = {}
    titles: dict[tuple[str, str], str] = {}
    for it in items:
        key = it.get(slug_key)
        if not key:
            # Items without a normalized key (e.g. hook_pattern unknown) don't
            # generate an aggregation page — they only show in Latest / niche.
            continue
        gk = (it["lang"], key)
        grouped.setdefault(gk, []).append(it)
        titles.setdefault(gk, it.get(group_key) or key)

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
            **{title_field: titles[(lang, key)]},
            items=[
                {**e, "relpath": _relpath_from_index(e, index_dir)}
                for e in entries
            ],
        )
        out_path.write_text(rendered, encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)} ({len(entries)} items)")


def _replace_block(text: str, marker: str, body: str) -> str:
    pat = re.compile(
        rf"(<!-- AUTO_GENERATED_{marker}_START -->)(.*?)(<!-- AUTO_GENERATED_{marker}_END -->)",
        re.DOTALL,
    )
    return pat.sub(lambda m: m.group(1) + body + m.group(3), text)


def _build_featured_block(lang_items: list[dict], lang: str) -> str:
    """Top-1 by views, rendered as a 'Today's top breakdown' card."""
    if not lang_items:
        return "\n_No breakdown published yet._\n"
    top = max(lang_items, key=lambda x: int(x["views"] or 0))
    rel = "./" + str(top["path"].relative_to(ROOT)).replace("\\", "/")
    label_views = "views" if lang == "en" else "次播放"
    label_full = "Full breakdown →" if lang == "en" else "完整拆解 →"
    parts = ["\n"]
    if top.get("thumbnail") and top.get("video_url"):
        parts.append(
            f'<a href="{top["video_url"]}" target="_blank">'
            f'<img src="{top["thumbnail"]}" alt="{top["title"]}" width="240" align="left" /></a>\n\n'
        )
    parts.append(f"**[{top['title']}]({rel})** — {top['views_formatted']} {label_views}\n\n")
    parts.append(f"[{label_full}]({rel})\n\n")
    parts.append("<br clear=\"left\" />\n")
    return "".join(parts)


def _build_niche_block(lang_items: list[dict], lang: str) -> str:
    """Render `[niche-a](...) · [niche-b](...)` with only niches that actually exist."""
    buckets: dict[str, dict] = {}
    for it in lang_items:
        slug = it.get("niche_slug")
        if not slug:
            continue
        b = buckets.setdefault(slug, {"label": it["niche"], "count": 0})
        b["count"] += 1
    if not buckets:
        return "\n_No niches published yet._\n"
    sep = " · "
    entries = sorted(buckets.items(), key=lambda kv: -kv[1]["count"])
    label = lambda x: x.replace("-", " ").title()
    out = sep.join(
        f"[{label(b['label'])}](./by-niche/{lang}/{slug}.md) ({b['count']})"
        for slug, b in entries
    )
    return "\n" + out + "\n"


def _build_pattern_block(lang_items: list[dict], lang: str) -> str:
    buckets: dict[str, dict] = {}
    for it in lang_items:
        slug = it.get("hook_pattern_slug")
        if not slug:
            continue
        b = buckets.setdefault(slug, {"label": it["hook_pattern"], "count": 0})
        b["count"] += 1
    if not buckets:
        return "\n_Hook patterns will appear here once enough breakdowns are tagged._\n"
    sep = " · "
    entries = sorted(buckets.items(), key=lambda kv: -kv[1]["count"])
    label = lambda x: x.replace("-", " ").title()
    out = sep.join(
        f"[{label(b['label'])}](./by-pattern/{lang}/hook-{slug}.md) ({b['count']})"
        for slug, b in entries
    )
    return "\n" + out + "\n"


def _build_latest_block(lang_items: list[dict], lang: str = "en") -> str:
    lang_items = sorted(lang_items, key=lambda x: -int(x["views"] or 0))
    top = lang_items[:10]
    if not top:
        return "\n_No breakdowns published yet._\n"
    label_views = "views" if lang == "en" else "次播放"
    lines = []
    for it in top:
        rel = "./" + str(it["path"].relative_to(ROOT)).replace("\\", "/")
        tag = f" · `{it['hook_pattern']}`" if it.get("hook_pattern") else ""
        lines.append(f"- [{it['title']}]({rel}) — {it['views_formatted']} {label_views}{tag} · {it['posted_date']}")
    return "\n" + "\n".join(lines) + "\n"


def _rewrite_readme(items: list[dict], readme_path: Path, lang: str) -> None:
    if not readme_path.exists():
        return
    text = readme_path.read_text(encoding="utf-8")
    lang_items = [i for i in items if i["lang"] == lang]

    new_text = text
    new_text = _replace_block(new_text, "FEATURED", _build_featured_block(lang_items, lang))
    new_text = _replace_block(new_text, "NICHE", _build_niche_block(lang_items, lang))
    new_text = _replace_block(new_text, "PATTERN", _build_pattern_block(lang_items, lang))
    new_text = _replace_block(new_text, "LATEST", _build_latest_block(lang_items, lang))

    if new_text != text:
        readme_path.write_text(new_text, encoding="utf-8")
        print(f"refreshed README {readme_path.name} (featured + niche + pattern + latest)")


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

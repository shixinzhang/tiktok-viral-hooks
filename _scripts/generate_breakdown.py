"""Daily breakdown generator.

Flow:
  1. GET /api/internal/breakdowns/recent (or use a built-in fixture in --dry-run)
  2. Skip slugs in _data/_takedowns.json and slugs already in _data/all-videos.json
  3. For each fresh item:
       - render English breakdown markdown
       - translate the body to Chinese via /api/internal/translate-markdown
       - render Chinese breakdown markdown
       - write both files to breakdowns/{lang}/{YYYY-MM}/{slug}.md
       - POST /api/internal/breakdowns/{slug}/github-url for each lang
       - update _data/all-videos.json

Usage:
  python _scripts/generate_breakdown.py                  # production run
  python _scripts/generate_breakdown.py --dry-run        # use fixture, write to stdout, skip backend writes
  python _scripts/generate_breakdown.py --limit 3        # cap items processed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from anchor_pool import pick_anchor


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "_data"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
BREAKDOWNS_DIR = ROOT / "breakdowns"

REPO_OWNER = "shixinzhang"
REPO_NAME = "tiktok-viral-hooks"


# ---------- fixture for --dry-run ----------

_FIXTURE = [
    {
        "slug": "fixture-creator-x-10m-views",
        "url": "https://www.tiktok.com/@fixture_creator/video/9999999999999999999",
        "title": "How Creator X Got 10M Views: Full Hook Breakdown",
        "thumbnail": None,
        "platform": "tiktok",
        "language": "en",
        "creator": "fixture_creator",
        "view_count": 10_400_000,
        "posted_date": "2026-05-22 14:22:00",
        "niche": "marketing",
        "hook_pattern": "curiosity-gap",
        "subtitle_json": {
            "full_text": "I'm not supposed to say this but the algorithm rewards exactly five things. "
                         "First, you need a pattern interrupt in the first 0.7 seconds. "
                         "Second, your hook has to imply forbidden knowledge. "
                         "Third, every six seconds you need a sub-claim. "
                         "Fourth, the payoff has to loop back to the hook. "
                         "And fifth, your CTA has to be implicit, never explicit.",
            "uploader": "fixture_creator",
            "duration": 47,
            "like_count": 1_200_000,
        },
        "summary_markdown": "Short fixture summary.",
        "viral_breakdown_markdown": (
            "### Hook (0-3s)\n"
            "The phrase 'I'm not supposed to say this' triggers a curiosity gap by "
            "implying forbidden knowledge.\n\n"
            "### Retention\n"
            "A numeric promise ('exactly five things') sets a finite, achievable goal.\n\n"
            "### Payoff\n"
            "The fifth point loops back to the hook, closing the cognitive open loop."
        ),
        "viral_breakdown_json": {
            "viral_score": 87,
            "hook": {
                "verbatim": "I'm not supposed to say this but the algorithm rewards exactly five things.",
                "highlighted_phrase": "not supposed to say this",
                "pattern": "curiosity-gap",
                "explanation": "Implies forbidden insider knowledge.",
            },
        },
        "mindmap_markdown": (
            "```mermaid\n"
            "mindmap\n"
            "  root((How Creator X Got 10M Views))\n"
            "    Hook 0-3s\n"
            "      Curiosity Gap\n"
            "      Forbidden knowledge framing\n"
            "    Retention 3-30s\n"
            "      Number promise (5)\n"
            "      Sub-claim every 6s\n"
            "    Payoff 30-47s\n"
            "      Loop back to hook\n"
            "    CTA\n"
            "      Implicit\n"
            "```"
        ),
        "github_breakdown_url": None,
    }
]


# ---------- IO helpers ----------

def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _outline_to_mermaid(text: str, fallback_root: str = "Mind Map") -> str:
    """Convert a markdown outline (#/##/###/-) to Mermaid mindmap syntax.

    GitHub renders ```mermaid + mindmap natively. The backend's
    mindmap_markdown is a plain outline; we adapt it here so the
    published page shows an actual visual mind map.

    Safety guard: Mermaid's mindmap renderer chokes on non-ASCII text
    (Chinese, Thai, emoji, etc.) — it either drops the node or renders
    boxes. When the outline contains any non-ASCII character we leave
    it as a markdown outline, which GitHub renders as nested bullets:
    readable, just not visual.
    """
    if not text:
        return text or ""
    if not all(ord(c) < 128 for c in text):
        return text
    lines = text.splitlines()
    nodes: list[tuple[int, str]] = []
    root_text = fallback_root
    cur_heading_depth = 0

    for line in lines:
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        m_h = re.match(r'^(#+)\s+(.+)$', stripped)
        if m_h:
            level = len(m_h.group(1))
            text_part = m_h.group(2).strip()
            if level == 1:
                root_text = text_part
                cur_heading_depth = 0
                continue
            depth = level - 1
            cur_heading_depth = depth
            nodes.append((depth, text_part))
            continue
        m_b = re.match(r'^(\s*)[-*+]\s+(.+)$', line)
        if m_b:
            indent_spaces = len(m_b.group(1))
            bullet_depth = cur_heading_depth + 1 + (indent_spaces // 2)
            text_part = m_b.group(2).strip()
            text_part = re.sub(r'\*+', '', text_part)
            text_part = re.sub(r'`+', '', text_part)
            nodes.append((bullet_depth, text_part))

    if not nodes:
        return text or ""

    def _clean(s: str) -> str:
        s = re.sub(r'[\[\](){}|]', '', s)
        s = s.replace('"', "'").strip()
        return s[:80]

    out = ["```mermaid", "mindmap", f"  root(({_clean(root_text)[:60] or 'Mind Map'}))"]
    for depth, text_part in nodes:
        clean = _clean(text_part)
        if not clean:
            continue
        out.append("  " * (depth + 1) + clean)
    out.append("```")
    return "\n".join(out)


def _format_views(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _truncate_60_percent(text: str) -> str:
    """Return first 60% of characters, ending at a sentence boundary when possible."""
    text = (text or "").strip()
    if not text:
        return ""
    cutoff = max(1, int(len(text) * 0.6))
    head = text[:cutoff]
    last_sentence = max(head.rfind("."), head.rfind("。"), head.rfind("!"), head.rfind("?"))
    if last_sentence > cutoff * 0.5:
        head = head[:last_sentence + 1]
    return head


def _yyyy_mm(posted_date: str) -> str:
    """Extract YYYY-MM from an ISO-ish timestamp, fallback to current month."""
    if posted_date:
        m = re.match(r"(\d{4})-(\d{2})", posted_date)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _creator_url(creator: str) -> str:
    handle = creator.lstrip("@") if creator else "unknown"
    return f"https://www.tiktok.com/@{handle}"


# ---------- backend client ----------

class BackendClient:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.headers = {"X-Internal-Key": key}
        self.client = httpx.Client(timeout=60)

    def recent(self, limit: int):
        r = self.client.get(
            f"{self.base}/api/internal/breakdowns/recent",
            params={"since_hours": 24, "min_views": 100_000, "limit": limit},
            headers=self.headers,
        )
        r.raise_for_status()
        return r.json()["data"]["items"]

    def by_slug(self, slug: str):
        r = self.client.get(
            f"{self.base}/api/internal/breakdowns/by-slug/{slug}",
            headers=self.headers,
        )
        r.raise_for_status()
        return r.json()["data"]

    def translate(self, text: str, target_language: str) -> str:
        r = self.client.post(
            f"{self.base}/api/internal/translate-markdown",
            headers=self.headers,
            json={"text": text, "target_language": target_language},
        )
        r.raise_for_status()
        return r.json()["data"]["translated"]

    def set_github_url(self, slug: str, lang: str, url: str) -> None:
        r = self.client.post(
            f"{self.base}/api/internal/breakdowns/{slug}/github-url",
            headers=self.headers,
            json={"url": url, "lang": lang},
        )
        r.raise_for_status()


# ---------- rendering ----------

def _build_context(item: dict, lang: str, breakdown_body: str, translated: bool,
                   original_language: str, available_languages: list[str]) -> dict:
    slug = item["slug"]
    subtitle = item.get("subtitle_json") or {}
    full_text = subtitle.get("full_text", "") if isinstance(subtitle, dict) else ""
    likes = subtitle.get("like_count", 0) if isinstance(subtitle, dict) else 0
    duration = subtitle.get("duration", 0) if isinstance(subtitle, dict) else 0
    creator = item.get("creator") or "unknown"

    transcript_60 = _truncate_60_percent(full_text)
    if translated and full_text:
        # Don't re-translate the transcript — just show original-language excerpt with a note.
        transcript_60 = full_text[:int(len(full_text) * 0.6)]

    # Language switcher links (relative paths between language siblings).
    switcher_parts = []
    for code in available_languages:
        label = {"en": "English", "zh-CN": "中文"}.get(code, code)
        if code == lang:
            switcher_parts.append(f"**{label}**")
        else:
            switcher_parts.append(f"[{label}](../../{code}/{_yyyy_mm(item.get('posted_date'))}/{slug}.md)")
    language_switcher = " · ".join(switcher_parts)

    viral_json = item.get("viral_breakdown_json") or {}
    hook_obj = viral_json.get("hook") or {}
    tldr = hook_obj.get("explanation") or hook_obj.get("verbatim") or "Why this video went viral, in one minute."

    title = item.get("title") or f"Viral breakdown: {slug}"
    description = (tldr[:155] + "…") if len(tldr) > 158 else tldr

    return {
        "title": title,
        "description": description,
        "lang": lang,
        "slug": slug,
        "available_languages": available_languages,
        "original_language": original_language,
        "translated": translated,
        "creator": f"@{creator.lstrip('@')}" if creator else "@unknown",
        "creator_url": _creator_url(creator),
        "video_url": item.get("url") or "",
        "views": int(item.get("view_count") or 0),
        "likes": int(likes or 0),
        "posted_date": (item.get("posted_date") or "")[:10],
        "niche": item.get("niche") or "other",
        "hook_pattern": item.get("hook_pattern"),
        "duration_seconds": int(duration or 0),
        "keywords": [item.get("niche") or "tiktok", "viral", item.get("hook_pattern") or "hook"],
        "generated_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "views_formatted": _format_views(item.get("view_count")),
        "tldr": tldr,
        "breakdown_body": breakdown_body,
        "thumbnail": item.get("thumbnail") or "",
        "mindmap_markdown": _outline_to_mermaid(
            item.get("mindmap_markdown") or "",
            fallback_root=title[:60] or "Mind Map",
        ) or "_(no mind map available)_",
        "transcript_first_60_percent": transcript_60 or "_(transcript not available)_",
        "language_switcher": language_switcher,
        "tool_anchor_attribution": pick_anchor(lang, seed=f"{slug}-attrib"),
        "tool_anchor_footer": pick_anchor(lang, seed=f"{slug}-footer"),
    }


def _render(env: Environment, item: dict, lang: str, breakdown_body: str,
            translated: bool, original_language: str, available_languages: list[str]) -> str:
    ctx = _build_context(item, lang, breakdown_body, translated, original_language, available_languages)
    tpl = env.get_template("breakdown.md.j2")
    return tpl.render(**ctx)


def _output_path(slug: str, lang: str, posted_date: str) -> Path:
    return BREAKDOWNS_DIR / lang / _yyyy_mm(posted_date) / f"{slug}.md"


def _github_url(slug: str, lang: str, posted_date: str) -> str:
    rel = f"breakdowns/{lang}/{_yyyy_mm(posted_date)}/{slug}.md"
    return f"https://github.com/{REPO_OWNER}/{REPO_NAME}/blob/main/{rel}"


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Use built-in fixture, write to stdout, no backend writes")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--rerender-existing", action="store_true",
                        help="Re-fetch every slug in _data/all-videos.json and overwrite the markdown files. "
                             "Used after template / converter changes.")
    args = parser.parse_args(argv)

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(disabled_extensions=("j2",), default_for_string=False),
        trim_blocks=False, lstrip_blocks=False,
    )

    takedowns = set(_load_json(DATA_DIR / "_takedowns.json", []))
    all_videos = _load_json(DATA_DIR / "all-videos.json", {})

    if args.dry_run:
        items = _FIXTURE[: args.limit]
        client = None
        print(f"[dry-run] using {len(items)} fixture item(s)", file=sys.stderr)
    else:
        base = os.environ.get("TOKTRANSCRIPT_API_BASE")
        key = os.environ.get("INTERNAL_API_KEY")
        if not base or not key:
            print("ERROR: TOKTRANSCRIPT_API_BASE and INTERNAL_API_KEY env vars required",
                  file=sys.stderr)
            return 2
        client = BackendClient(base, key)
        if args.rerender_existing:
            slugs = list(all_videos.keys())
            items = []
            for slug in slugs:
                try:
                    items.append(client.by_slug(slug))
                except Exception as e:
                    print(f"by_slug failed for {slug}: {e}", file=sys.stderr)
            print(f"rerender-existing: pulled {len(items)} of {len(slugs)} slug(s)", file=sys.stderr)
        else:
            items = client.recent(args.limit)
            print(f"fetched {len(items)} candidate(s) from backend", file=sys.stderr)

    processed = 0
    for item in items:
        slug = item.get("slug")
        if not slug:
            continue
        if slug in takedowns:
            print(f"skip {slug}: takedown", file=sys.stderr)
            continue
        already = set((all_videos.get(slug) or {}).get("languages", []))
        if {"en", "zh-CN"}.issubset(already) and not args.dry_run and not args.rerender_existing:
            print(f"skip {slug}: already published in en+zh-CN", file=sys.stderr)
            continue

        original_language = (item.get("language") or "en").lower()
        available_languages = ["en", "zh-CN"]
        source_body = item.get("viral_breakdown_markdown") or ""

        def _matches(src: str, tgt: str) -> bool:
            """src='zh' matches tgt='zh-CN'; src='en' matches tgt='en'."""
            src = src.lower()
            tgt = tgt.lower()
            return src == tgt or tgt.startswith(src + "-") or src.startswith(tgt + "-")

        def _body_for(target_lang: str) -> str:
            if not source_body:
                return ""
            if _matches(original_language, target_lang):
                return source_body
            if client is None:
                return f"（dry-run: would translate to {target_lang}）\n\n{source_body}"
            try:
                return client.translate(source_body, target_lang)
            except Exception as e:
                print(f"translate({target_lang}) failed for {slug}: {e}", file=sys.stderr)
                return source_body

        en_body = _body_for("en")
        zh_body = _body_for("zh-CN")

        en_md = _render(env, item, "en", en_body,
                        translated=not _matches(original_language, "en"),
                        original_language=original_language,
                        available_languages=available_languages)
        zh_md = _render(env, item, "zh-CN", zh_body,
                        translated=not _matches(original_language, "zh-CN"),
                        original_language=original_language,
                        available_languages=available_languages)

        if args.dry_run:
            print("=" * 80)
            print(f"[EN] {_output_path(slug, 'en', item.get('posted_date'))}")
            print("=" * 80)
            print(en_md)
            print("=" * 80)
            print(f"[ZH-CN] {_output_path(slug, 'zh-CN', item.get('posted_date'))}")
            print("=" * 80)
            print(zh_md)
        else:
            for lang_code, body in (("en", en_md), ("zh-CN", zh_md)):
                out = _output_path(slug, lang_code, item.get("posted_date"))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(body, encoding="utf-8")
                if client is not None:
                    try:
                        client.set_github_url(slug, lang_code, _github_url(slug, lang_code, item.get("posted_date")))
                    except Exception as e:
                        print(f"github-url writeback failed for {slug}/{lang_code}: {e}", file=sys.stderr)
            all_videos[slug] = {
                "languages": sorted(set(list(already) + available_languages)),
                "niche": item.get("niche") or "other",
                "hook_pattern": item.get("hook_pattern"),
                "posted_date": item.get("posted_date"),
                "view_count": int(item.get("view_count") or 0),
                "title": item.get("title") or "",
            }
        processed += 1

    if not args.dry_run:
        _save_json(DATA_DIR / "all-videos.json", all_videos)
    print(f"processed {processed} item(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

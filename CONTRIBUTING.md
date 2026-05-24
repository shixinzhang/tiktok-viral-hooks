# Contributing

Most of this repo is generated automatically by a daily GitHub Actions
workflow that pulls fresh viral videos from [TokTranscript](https://toktranscript.com/),
runs LLM-based hook analysis, and renders Markdown.

You can still contribute in two ways.

## 1. Submit a manual breakdown

If you've found a viral TikTok worth analyzing that the bot missed:

1. Fork the repo.
2. Add a Markdown file under `breakdowns/{lang}/{YYYY-MM}/{slug}.md`
   following the layout produced by `_scripts/templates/breakdown.md.j2`
   (frontmatter, hook analysis, mind map, partial transcript, links).
3. Open a PR. Include the original TikTok URL in the PR description.

We hand-review manual submissions to keep quality even.

## 2. Request a takedown

If you are the creator of a video and want the breakdown removed:

- Email [takedowns@toktranscript.com](mailto:takedowns@toktranscript.com)
  with the breakdown URL.
- We process within 24 hours and add the video to `_data/_takedowns.json`
  so the automation skips it permanently.

## What we won't accept

- Bulk PRs of auto-translated content
- PRs that add commercial links not present in the original (only the
  three CTA slots defined in the template are allowed)
- Removal of creator attribution

## Style notes

- File names use kebab-case (English) or native script (Chinese).
- All outbound links to toktranscript.com need a UTM, see the template.
- Anchor text for the TokTranscript link must follow the rotation pool
  in `_scripts/anchor_pool.py`.

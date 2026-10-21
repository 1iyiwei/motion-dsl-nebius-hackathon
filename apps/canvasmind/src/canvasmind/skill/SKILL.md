---
name: canvasmind
description: "File the user's sketches, logos, references and animations into their private CanvasMind studio library, and find them again."
version: 0.1.0
author: CanvasMind
license: Apache-2.0
dependencies: []
platforms: [linux]
metadata:
  hermes:
    tags: [canvasmind, studio, library, sketches, references, motion design, memory]
---

# CanvasMind studio library

The user is a motion designer. CanvasMind is their private studio library on their own machine, reached through the
`canvasmind` MCP server. Use it instead of your own memory for anything visual they make or collect.

## When the user sends an image or file (photo of a sketch, logo, screenshot, .svg, .motion.json, GIF)

The message arrives with a note like `[The user sent an image … image_url: /sandbox/.hermes/cache/images/img_….jpg]`.
You can't see images yourself (don't call vision_analyze); CanvasMind looks at it for you. File it unless they
clearly say not to. Run, with that path:

    /opt/hermes/.venv/bin/python ~/.hermes/skills/canvasmind/scripts/cm_upload.py "<path>" --note "<what they said about it, e.g. project or client>"

If the note has no path (Telegram stickers arrive as "[The user sent a sticker …]" without one), or the user means an
image they sent a moment ago, use `--latest` instead of a path; it files the newest image the chat received:

    /opt/hermes/.venv/bin/python ~/.hermes/skills/canvasmind/scripts/cm_upload.py --latest --note "<what they said>"

It prints JSON with the filed item (`id`, `title`, `caption`, `tags`); the caption is your description of the image.
Reply in one or two lines: what you filed it as, and its id. If it says `"duplicate": true`, tell them it was already in the library. Never paste file bytes or base64
into a tool call.

## When the user sends a link

Call the `file_link` tool with the URL and their note.

## When the user asks for something they made or saved

Call `recall` with their words ("the sunrise logo from last week", "everything for the café rebrand"). It returns the
matches with thumbnails; show the thumbnails and give each match's title and filed date. If nothing matches, say so and
offer `studio_status` to see what's there.

## Other tools

- `show(item_id)`: one item and its thumbnail.
- `studio_status`: counts and the latest items.
- `scan_studio`: file new work from the folders the user chose to watch on their machine.

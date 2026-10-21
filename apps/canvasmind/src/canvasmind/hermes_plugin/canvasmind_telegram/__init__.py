"""Hermes plugin: two fixes to the Telegram adapter that CanvasMind depends on.

1. SVG documents. The adapter sends every `image/*` document to the raster image cache, which refuses SVG ("Refusing
   to cache non-image data as .jpg"), and the file is dropped. We wrap that cache function: SVG bytes are saved to the
   document cache as .svg and the path is returned, so the agent gets the file's path and can file it.
2. Local GIFs. `send_multiple_images` only treats non-`file://` URLs as animations, so a GIF returned by a tool (cached
   locally) goes out as a photo and Telegram shows its first frame. We send local .gif files with `send_animation`.

Hermes's plugin rules ask plugins not to touch core; these are narrow wrappers that fall through to the original code
for everything else, and they are reported upstream.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from urllib.parse import unquote

logger = logging.getLogger(__name__)


def _is_svg(data: bytes) -> bool:
    head = bytes(data[:4096]).lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _patch_svg_cache() -> None:
    from gateway.platforms import base

    original = base.cache_image_from_bytes
    if getattr(original, "_canvasmind", False):
        return

    def cache_image_from_bytes(data, ext=".jpg", *args, **kwargs):
        if _is_svg(data):
            folder = base.get_document_cache_dir()
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"doc_{secrets.token_hex(6)}.svg"
            path.write_bytes(bytes(data))
            logger.info("canvasmind_telegram: kept SVG document at %s", path)
            return str(path)
        return original(data, ext, *args, **kwargs)

    cache_image_from_bytes._canvasmind = True
    base.cache_image_from_bytes = cache_image_from_bytes


def _local_gif(url: str) -> str | None:
    path = unquote(url[7:]) if url.startswith("file://") else url
    return path if path.lower().endswith(".gif") and os.path.isfile(path) else None


def _adapter_classes():
    """Every loaded TelegramAdapter class: Hermes can import the adapter module under more than one name
    (plugins.platforms.telegram.adapter and hermes_plugins.telegram_platform.adapter), and only one is live."""
    seen = {}
    for m in list(sys.modules.values()):
        cls = getattr(m, "TelegramAdapter", None)
        if isinstance(cls, type) and hasattr(cls, "send_multiple_images"):
            seen[id(cls)] = cls
    return list(seen.values())


def _patch_gif_send() -> int:
    """Patch every not-yet-patched TelegramAdapter class; returns how many were patched now."""
    patched = 0
    for adapter_cls in _adapter_classes():
        original = adapter_cls.send_multiple_images
        if getattr(original, "_canvasmind", False):
            continue
        adapter_cls.send_multiple_images = _wrap(original)
        patched += 1
        logger.info("canvasmind_telegram: GIF patch installed on %s.%s", adapter_cls.__module__, adapter_cls.__name__)
    return patched


def _wrap(original):
    async def send_multiple_images(self, chat_id, images, metadata=None, human_delay=0.0):
        gifs = [(img, p) for img in images if (p := _local_gif(str(img[0])))]
        rest = [img for img in images if not _local_gif(str(img[0]))]
        result = None
        for img, path in gifs:
            caption = img[1] if len(img) > 1 else None
            from telegram import InputFile

            with open(path, "rb") as f:  # with a .gif filename, or Telegram files it as application/octet-stream
                gif = InputFile(f.read(), filename=os.path.basename(path))
            result = await self.send_animation(chat_id, gif, caption=caption, metadata=metadata)
            logger.info("canvasmind_telegram: sent %s as an animation (ok=%s)", os.path.basename(path), result.success)
        if rest or result is None:
            result = await original(self, chat_id, rest, metadata, human_delay)
        return result

    send_multiple_images._canvasmind = True
    return send_multiple_images


def _pre_llm_call(**kwargs):
    try:
        _patch_gif_send()
    except Exception as e:
        logger.warning("canvasmind_telegram: GIF patch not installed: %s", e)
    return None


def register(ctx):
    logger.info("canvasmind_telegram: registering")
    for patch in (_patch_svg_cache, _patch_gif_send):
        try:
            patch()
        except Exception as e:  # never break the gateway over this
            logger.warning("canvasmind_telegram: %s not installed: %s", patch.__name__, e)
    ctx.register_hook("pre_llm_call", _pre_llm_call)

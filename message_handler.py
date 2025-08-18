import discord
import logging
import re
from io import BytesIO
from typing import List, Tuple, Optional, Dict
from utils import (
    strip_banner, _suppress_url_embeds, _shorten, _delink_for_reply, 
    is_image_attachment, build_jump_url, _is_command_text, _is_filler, 
    _apply_abbreviations, CUSTOM_EMOJI_RE, UNICODE_EMOJI_RE, PUNCT_GAP_RE, URL_RE
)

logger = logging.getLogger(__name__)

REPLY_ICON_DEFAULT = "↪"
REPLY_LABEL_EN = "REPLY"
REPLY_LABEL_ZH = "回复"
PREVIEW_LIMIT = 90
REPLY_PREVIEW_LIMIT = 50

class MessageHandler:
    def __init__(self, bot, translator, gpt_handler, mirror_manager, config, guild_dicts, passthrough_cfg, guild_abbrs):
        self.bot = bot
        self.translator = translator
        self.gpt_handler = gpt_handler
        self.mirror_manager = mirror_manager
        self.config = config
        self.guild_dicts = guild_dicts
        self.passthrough_cfg = passthrough_cfg
        self.guild_abbrs = guild_abbrs
        self._recent_user_message: Dict[int, int] = {}

    async def is_pass_through(self, msg) -> bool:
        t = (msg.content or "")
        t2 = CUSTOM_EMOJI_RE.sub("", t)
        t2 = UNICODE_EMOJI_RE.sub("", t2)
        t2 = PUNCT_GAP_RE.sub("", t2)
        if not t2 and not msg.attachments:
            return True
        if URL_RE.fullmatch(t.strip()):
            return True
        gid = str(msg.guild.id)
        if _is_command_text(gid, msg.content, self.passthrough_cfg):
            return True
        if _is_filler(msg.content, gid, self.passthrough_cfg):
            return True
        return not re.search(r"[A-Za-z\u4e00-\u9fff]", t2)

    async def _get_ref_message(self, msg: discord.Message) -> Optional[discord.Message]:
        ref = msg.reference
        if not ref:
            return None
        if isinstance(ref.resolved, discord.Message):
            return ref.resolved
        try:
            if ref.message_id and (ref.channel_id == msg.channel.id):
                return await msg.channel.fetch_message(ref.message_id)
        except Exception:
            pass
        return None

    async def _fetch_message(self, guild: discord.Guild, channel_id: int, message_id: int) -> Optional[discord.Message]:
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except Exception:
                return None
        try:
            return await ch.fetch_message(message_id)
        except Exception:
            return None

    async def _get_original_author(self, ref: discord.Message) -> discord.User:
        if not ref.webhook_id:
            return ref.author
        
        try:
            gid = ref.guild.id if ref.guild else 0
            neighbors = self.mirror_manager.get_neighbors(gid, ref.id)
            
            for channel_id, message_id in neighbors.items():
                try:
                    original_msg = await self._fetch_message(ref.guild, channel_id, message_id)
                    if original_msg and not original_msg.webhook_id:
                        return original_msg.author
                except Exception:
                    continue
            
            guild_mirrors = self.mirror_manager.mirror_map.get(gid, {})
            for src_msg_id, channel_mappings in guild_mirrors.items():
                for ch_id, mapped_msg_id in channel_mappings.items():
                    if mapped_msg_id == ref.id:
                        try:
                            source_neighbors = self.mirror_manager.get_neighbors(gid, src_msg_id)
                            for source_ch_id, _ in source_neighbors.items():
                                if source_ch_id != ref.channel.id:
                                    source_msg = await self._fetch_message(ref.guild, source_ch_id, src_msg_id)
                                    if source_msg and not source_msg.webhook_id:
                                        return source_msg.author
                            
                            if src_msg_id != ref.id:
                                source_msg = await self._fetch_message(ref.guild, ch_id, src_msg_id)
                                if source_msg and not source_msg.webhook_id:
                                    return source_msg.author
                        except Exception:
                            continue
                            
        except Exception:
            pass
        
        return ref.author

    async def _choose_jump_and_preview(self, ref: discord.Message, target_lang: str, target_channel_id: int) -> tuple[str, str, bool]:
        gid = ref.guild.id if ref.guild else 0
        if ref.channel.id == target_channel_id:
            show_text = strip_banner(ref.content or "")
            only_image = (not show_text) and any(is_image_attachment(a) for a in ref.attachments)
            jump = build_jump_url(gid, ref.channel.id, ref.id)
            return jump, show_text, only_image
        mirror_id = self.mirror_manager.find_mirror_id(gid, ref.id, target_channel_id)
        if mirror_id:
            mirror_msg = await self._fetch_message(ref.guild, target_channel_id, mirror_id)
            if mirror_msg:
                mirror_text = strip_banner(mirror_msg.content or "")
                only_image = (not mirror_text) and any(is_image_attachment(a) for a in mirror_msg.attachments)
                jump = build_jump_url(gid, target_channel_id, mirror_id)
                return jump, mirror_text, only_image
        raw = strip_banner(ref.content or "")
        only_image = (not raw) and any(is_image_attachment(a) for a in ref.attachments)
        jump = ref.jump_url
        if only_image:
            return jump, "", True
        gid_str = str(ref.guild.id)
        cm = self.guild_dicts.get(gid_str, {})
        ref_lang = await self.gpt_handler.detect_language(raw)
        if target_lang == "Chinese" and ref_lang == "English":
            show = await self.translator.translate_text(raw, "en_to_zh", cm)
        elif target_lang == "English" and ref_lang == "Chinese":
            show = await self.translator.translate_text(raw, "zh_to_en", cm)
        else:
            show = raw
        return jump, show, False

    async def _make_top_reply_banner(self, ref: discord.Message, target_lang: str, target_channel_id: int, original_author: discord.User = None) -> str:
        reply_label = REPLY_LABEL_ZH if target_lang == "Chinese" else REPLY_LABEL_EN
        reply_icon = REPLY_ICON_DEFAULT
        jump, preview, only_image = await self._choose_jump_and_preview(ref, target_lang, target_channel_id)
        if only_image:
            preview = "[image]"
        preview = re.sub(r"\s+", " ", preview).strip()
        preview = _delink_for_reply(preview)
        preview = _shorten(preview, REPLY_PREVIEW_LIMIT)
        if original_author is None:
            original_author = await self._get_original_author(ref)
        return f"> {original_author.mention} {reply_icon} [{reply_label}]({jump}) {preview}".rstrip()

    async def send_via_webhook(self, webhook_url: str, target_channel_id: int, content: str, msg: discord.Message, *, lang: str):
        if not self.bot.session:
            raise RuntimeError("HTTP session not initialized")
        wh = discord.Webhook.from_url(webhook_url, session=self.bot.session)

        files_data: List[Tuple[str, bytes]] = []
        for att in msg.attachments:
            try:
                data = await att.read()
                files_data.append((att.filename, data))
            except Exception:
                logger.exception("read attachment failed")

        top_banner = ""
        ref = await self._get_ref_message(msg)
        if ref is not None:
            try:
                ref_original_author = None
                if ref.webhook_id:
                    ref_original_author = await self._get_original_author(ref)
                    if ref_original_author == ref.author and hasattr(ref, 'author') and hasattr(ref.author, 'display_name'):
                        logger.info(f"Could not find original author for webhook message {ref.id}, using display name: {ref.author.display_name}")
                        class WebhookUser:
                            def __init__(self, display_name):
                                self.display_name = display_name
                                self.mention = f"**{display_name}**"
                        
                        ref_original_author = WebhookUser(ref.author.display_name)
                else:
                    ref_original_author = ref.author
                
                top_banner = await self._make_top_reply_banner(ref, lang, target_channel_id, ref_original_author)
            except Exception:
                logger.exception("build top reply banner failed")

        body = (content or "").strip()
        body = _suppress_url_embeds(body)

        final_lines: List[str] = []
        if top_banner:
            final_lines.append(top_banner)
        if body:
            final_lines.append(body)
        final = "\n".join(final_lines)

        try:
            sent = await wh.send(
                content=final or None,
                username=msg.author.display_name,
                avatar_url=(msg.author.avatar.url if msg.author.avatar else None),
                files=[discord.File(fp=BytesIO(d), filename=fn) for fn, d in files_data] or [],
                allowed_mentions=self.bot.no_ping,
                wait=True,
            )
            try:
                if isinstance(sent, (discord.Message, discord.WebhookMessage)):
                    self.mirror_manager.add(msg.guild.id, msg.id, target_channel_id, int(sent.id))
                    self.mirror_manager.add(msg.guild.id, int(sent.id), msg.channel.id, msg.id)
            except Exception:
                logger.exception("mirror map save failed")
        except Exception:
            logger.exception("Webhook send failed")
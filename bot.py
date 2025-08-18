#!/usr/bin/env python3
import asyncio
import os
import logging
from typing import Optional, Dict
import aiohttp
import discord
from discord.ext import commands, tasks
from openai import AsyncOpenAI
import deepl
from dotenv import load_dotenv

from translator import Translator
from gpt_handler import GPTHandler
from message_handler import MessageHandler
from mirror_manager import MirrorManager
from utils import _load_json_or, _apply_abbreviations, strip_banner, _is_command_text
from preprocess import preprocess
import joy_cmds as prompt_mod
import health_server
from storage import storage

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE, "config.json")
DICTIONARY_PATH = os.path.join(BASE, "dictionary.json")
ABBREV_PATH = os.path.join(BASE, "abbreviations.json")
PASSTHROUGH_PATH = os.path.join(BASE, "passthrough.json")

config = _load_json_or(CONFIG_PATH, {})

config["discord_token"] = os.getenv("DISCORD_TOKEN", config.get("discord_token", ""))
config["openai_key"] = os.getenv("OPENAI_KEY", os.getenv("OPENAI_API_KEY", config.get("openai_key", "")))
config["deepl_key"] = "adef608f-1d8b-4831-94a2-37a6992c77d8:fx"

openai_client = AsyncOpenAI(api_key=config["openai_key"]) if config.get("openai_key") else None
deepl_client = deepl.Translator(config["deepl_key"])

if config.get("openai_key"):
    mask = config["openai_key"][:4] + "..." + config["openai_key"][-4:]
    logger.info(f"OpenAI API Key loaded: {mask}")
else:
    logger.error("MISSING: OpenAI API Key not found!")

if config.get("deepl_key"):
    mask_deepl = config["deepl_key"][:4] + "..." + config["deepl_key"][-4:]
    logger.info(f"DeepL API Key loaded: {mask_deepl}")
else:
    logger.error("MISSING: DeepL API Key not found!")

if config.get("discord_token"):
    mask_token = config["discord_token"][:10] + "..." + config["discord_token"][-10:]
    logger.info(f"Discord Token loaded: {mask_token}")
else:
    logger.error("MISSING: Discord Token not found!")

guild_dicts = {}
guild_abbrs = {"default": {}}
passthrough_cfg = {"default": {"commands": [], "fillers": []}}

MIRROR_PATH = os.path.join(BASE, config.get("mirror_store_path", "mirror.json"))
MIRROR_MAX_PER_GUILD = int(config.get("mirror_prune_max_per_guild", 4000))

class TranslatorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.session: Optional[aiohttp.ClientSession] = None
        self.no_ping = discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=False)
        self.health_runner = None
        
        self.gpt_handler = GPTHandler(openai_client)
        self.mirror_manager = MirrorManager(MIRROR_PATH, MIRROR_MAX_PER_GUILD)
        self.translator = Translator(deepl_client, self.gpt_handler)
        self.message_handler = MessageHandler(
            self, self.translator, self.gpt_handler, self.mirror_manager, 
            config, guild_dicts, passthrough_cfg, guild_abbrs
        )

    async def setup_hook(self):
        global guild_dicts, guild_abbrs, passthrough_cfg
        
        logger.info("Loading persistent data...")
        guild_dicts.update(await storage.load_json("dictionary", {}))
        
        hardcoded_defaults = {
            "default": {
                "wc": "卧槽", "nb": "牛逼", "666": "厉害", "xswl": "笑死我了",
                "glhf": "good luck have fun", "afk": "away from keyboard", 
                "brb": "be right back", "idk": "I don't know", "idc": "I don't care",
                "ikr": "I know right", "imo": "in my opinion", "btw": "by the way",
                "tbh": "to be honest", "ngl": "not gonna lie", "lmk": "let me know",
                "omg": "oh my god", "wtf": "what the fuck", "wth": "what the hell",
                "smh": "shaking my head", "lol": "laughing out loud", 
                "lmao": "laughing my ass off", "nvm": "never mind", 
                "asap": "as soon as possible", "aka": "also known as",
                "irl": "in real life", "dm": "direct message", "np": "no problem",
                "ty": "thank you", "thx": "thanks", "pls": "please", "plz": "please",
                "rn": "right now", "ppl": "people", "u": "you", "ur": "your",
                "ya": "yeah", "yea": "yeah", "bc": "because", "cuz": "because",
                "tho": "though", "fr": "for real", "rip": "rest in peace", "jk": "just kidding"
            }
        }
        
        logger.info(f"Attempting to load abbreviations from: {ABBREV_PATH}")
        logger.info(f"File exists: {os.path.exists(ABBREV_PATH)}")
        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"BASE directory: {BASE}")
        
        local_abbrs = _load_json_or(ABBREV_PATH, hardcoded_defaults)
        cloud_abbrs = await storage.load_json("abbreviations", {})
        
        logger.info(f"Local abbreviations loaded: {len(local_abbrs)} groups")
        logger.info(f"Local default abbreviations: {len(local_abbrs.get('default', {}))}")
        logger.info(f"Cloud abbreviations loaded: {len(cloud_abbrs)} groups")
        
        guild_abbrs.clear()
        guild_abbrs.update(local_abbrs)
        
        for guild_id, abbr_data in cloud_abbrs.items():
            if guild_id == "default":
                guild_abbrs["default"].update(abbr_data)
            else:
                guild_abbrs[guild_id] = abbr_data
        
        passthrough_cfg.update(_load_json_or(PASSTHROUGH_PATH, {"default": {"commands": [], "fillers": []}}))
        
        logger.info(f"Loaded {len(guild_dicts)} guilds in dictionary")
        default_abbr_count = len(guild_abbrs.get("default", {}))
        logger.info(f"Final result: {len(guild_abbrs)} abbreviation groups with {default_abbr_count} default abbreviations")
        logger.info(f"Default abbreviations sample: {list(guild_abbrs.get('default', {}).keys())[:10]}")
        
        self.mirror_manager.load()
        self.session = aiohttp.ClientSession()
        self.health_runner = await health_server.start_health_server()
        self.heartbeat_task.start()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        self.mirror_manager.save()
        self.heartbeat_task.cancel()
        if self.health_runner:
            await self.health_runner.cleanup()
        await super().close()
    
    @tasks.loop(seconds=30)
    async def heartbeat_task(self):
        health_server.update_bot_status(running=True)
    
    @heartbeat_task.before_loop
    async def before_heartbeat(self):
        await self.wait_until_ready()

    def _guild_cfg(self, gid: str) -> Optional[dict]:
        return config.get("guilds", {}).get(gid)

    def is_admin_user(self, g: discord.Guild, m: discord.Member) -> bool:
        gid = str(g.id)
        admin = config.setdefault("guilds", {}).setdefault(gid, {}).setdefault("admin", {})
        req = admin.get("require_manage_guild", True)
        allow_users = set(admin.get("allowed_user_ids", []))
        allow_roles = set(admin.get("allowed_role_ids", []))
        if allow_users and m.id in allow_users:
            return True
        if allow_roles and any(r.id in allow_roles for r in getattr(m, "roles", [])):
            return True
        if req:
            perms = getattr(m, "guild_permissions", None)
            return bool(perms and perms.manage_guild)
        return True

    async def _process_star_patch_if_any_with_content(self, content: str, msg: discord.Message):
        t = content.strip()
        
        if len(t) >= 2 and t.endswith("*") and "\n" not in t:
            if t.startswith("*") or t.startswith("**") or t.startswith("***"):
                logger.info(f"DEBUG: Skipping markdown format: '{t}'")
                return None
                
            star_count = t.count("*")
            if star_count > 1 and star_count % 2 == 0:
                inner_text = t[:-1]
                if "*" in inner_text:
                    logger.info(f"DEBUG: Skipping potential markdown pairs: '{t}'")
                    return None
            
            logger.info(f"DEBUG: Processing star patch: '{t}'")
            ref = await self.message_handler._get_ref_message(msg)
            base = None
            if ref and ref.author.id == msg.author.id:
                base = ref.content or ""
            else:
                last_id = self.message_handler._recent_user_message.get(msg.author.id)
                if last_id:
                    try:
                        base_msg = await msg.channel.fetch_message(last_id)
                        if base_msg and base_msg.author.id == msg.author.id:
                            base = base_msg.content or ""
                    except Exception:
                        base = None
            if base:
                patch_text = t[:-1].strip()
                base_text = base.strip()
                
                if not patch_text:
                    logger.info(f"DEBUG: Skipping empty patch: '{t}'")
                    return None
                    
                if base_text.endswith("*"):
                    logger.info(f"DEBUG: Skipping patch on patch: base '{base_text}' also ends with *")
                    return None
                    
                if patch_text == base_text:
                    logger.info(f"DEBUG: Skipping identical patch: '{patch_text}' same as base")
                    return None
                
                logger.info(f"DEBUG: Applying patch '{patch_text}' to base '{base_text}'")
                try:
                    fixed = await self.gpt_handler.apply_star_patch(strip_banner(base_text), patch_text)
                    logger.info(f"DEBUG: Patch result received: '{fixed}'")
                    if fixed and fixed.strip():
                        logger.info(f"DEBUG: Returning valid patch result with original msg ID: '{fixed}', {last_id}")
                        return (fixed, last_id)
                    else:
                        logger.error(f"DEBUG: Patch result is empty or None, returning None")
                        return None
                except Exception as e:
                    logger.error(f"DEBUG: Exception in apply_star_patch: {e}")
                    return None
            else:
                logger.info(f"DEBUG: No base message found for star patch")
        return None

    async def _handle_star_patch_edit(self, processed_content: str, msg: discord.Message, cfg: dict, gid: str, cm: dict, original_msg_id: int):
        logger.info(f"DEBUG: Handling star patch edit for content: '{processed_content}'")
        
        last_id = original_msg_id
        logger.info(f"DEBUG: Using original message ID from patch processing: {last_id}")
        if not last_id:
            logger.info("DEBUG: No original message ID provided for star patch edit")
            return
            
        logger.info(f"DEBUG: Looking for mirrors of original message {last_id}")
        logger.info(f"DEBUG: Current mirror_map has {len(self.mirror_manager.mirror_map.get(msg.guild.id, {}))} entries for this guild")
        
        gid_int = msg.guild.id
        guild_mirrors = self.mirror_manager.mirror_map.get(gid_int, {})
        logger.info(f"DEBUG: Full mirror_map for guild {gid_int}: {guild_mirrors}")
            
        try:
            neighbors = self.mirror_manager.get_neighbors(gid_int, last_id)
            if not neighbors:
                logger.info(f"DEBUG: No mirror messages found for original message {last_id}")
                logger.info(f"DEBUG: Available message IDs in mirror_map: {list(guild_mirrors.keys())}")
                
                for msg_id, channels in guild_mirrors.items():
                    logger.info(f"DEBUG: Message {msg_id} maps to channels: {channels}")
                return
            
            logger.info(f"DEBUG: Found {len(neighbors)} mirror messages for original message {last_id}: {neighbors}")
                
            txt = strip_banner(processed_content)
            lang = await self.gpt_handler.detect_language(txt)
            logger.info(f"DEBUG: Star patch detected language: '{lang}' for text: '{txt}'")
            
            async def to_target(text: str, direction: str) -> str:
                tr = await self.translator.translate_text(text, direction, cm)
                if tr == "/":
                    return text
                return tr
            
            is_en = msg.channel.id == cfg["en_channel_id"]
            is_zh = msg.channel.id == cfg["zh_channel_id"]
            
            for ch_id, mirror_msg_id in neighbors.items():
                try:
                    logger.info(f"DEBUG: Trying to edit mirror message {mirror_msg_id} in channel {ch_id}")
                    ch = self.get_channel(ch_id) or await self.fetch_channel(ch_id)
                    mirror_msg = await ch.fetch_message(mirror_msg_id)
                    logger.info(f"DEBUG: Found mirror message: '{mirror_msg.content[:50]}'")
                    
                    new_content = None
                    
                    if is_zh and ch_id == cfg["en_channel_id"]:
                        logger.info(f"DEBUG: Editing EN channel message from ZH channel")
                        if lang == "Chinese":
                            new_content = await to_target(txt, "zh_to_en")
                        elif lang == "English":
                            new_content = txt
                        else:
                            new_content = txt
                            
                    elif is_en and ch_id == cfg["zh_channel_id"]:
                        logger.info(f"DEBUG: Editing ZH channel message from EN channel")
                        if lang == "English":
                            new_content = await to_target(txt, "en_to_zh")
                        elif lang == "Chinese":
                            new_content = txt
                        else:
                            new_content = txt
                    
                    if new_content:
                        logger.info(f"DEBUG: Attempting to edit message to: '{new_content}'")
                        
                        if mirror_msg.webhook_id:
                            logger.info(f"DEBUG: Editing webhook message via webhook")
                            if ch_id == cfg["zh_channel_id"] and "zh_webhook_url" in cfg:
                                webhook_url = cfg["zh_webhook_url"]
                            elif ch_id == cfg["en_channel_id"] and "en_webhook_url" in cfg:
                                webhook_url = cfg["en_webhook_url"]
                            else:
                                logger.error(f"No webhook URL found for channel {ch_id}")
                                continue
                                
                            if not self.session:
                                logger.error("HTTP session not initialized")
                                continue
                                
                            wh = discord.Webhook.from_url(webhook_url, session=self.session)
                            await wh.edit_message(mirror_msg_id, content=new_content)
                            logger.info(f"DEBUG: Successfully edited webhook message {mirror_msg_id} to: '{new_content}'")
                        else:
                            await mirror_msg.edit(content=new_content)
                            logger.info(f"DEBUG: Successfully edited bot message {mirror_msg_id} to: '{new_content}'")
                    else:
                        logger.info(f"DEBUG: No content to edit for channel {ch_id}")
                        
                except Exception as e:
                    logger.error(f"Failed to edit mirror message {mirror_msg_id} in channel {ch_id}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    
        except Exception as e:
            logger.error(f"Star patch edit failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def on_message(self, msg: discord.Message):
        if msg.author.bot or msg.webhook_id or not msg.guild:
            return
        
        await self.process_commands(msg)
        gid = str(msg.guild.id)
        cfg = self._guild_cfg(gid)
        if not cfg:
            return
        is_en = msg.channel.id == cfg["en_channel_id"]
        is_zh = msg.channel.id == cfg["zh_channel_id"]
        if not (is_en or is_zh):
            return
        if _is_command_text(gid, msg.content, passthrough_cfg):
            return
        
        original_content = msg.content or ""
        patch_result = await self._process_star_patch_if_any_with_content(original_content, msg)
        if patch_result is not None:
            patched_content, original_msg_id = patch_result
        else:
            patched_content, original_msg_id = None, None
        
        self.message_handler._recent_user_message[msg.author.id] = msg.id
        
        cm = guild_dicts.get(gid, {})
        raw = msg.content or ""
        raw = await self.translator._preprocess_with_gpt_check(raw, "zh_to_en", cm)
        raw = _apply_abbreviations(raw, gid, guild_abbrs)
        
        if patched_content is not None:
            raw = await self.translator._preprocess_with_gpt_check(patched_content, "zh_to_en", cm)
            raw = _apply_abbreviations(raw, gid, guild_abbrs)
            
            await self._handle_star_patch_edit(raw, msg, cfg, gid, cm, original_msg_id)
            return
        
        class TempMessage:
            def __init__(self, content, attachments, guild):
                self.content = content
                self.attachments = attachments
                self.guild = guild
        
        temp_msg = TempMessage(raw, msg.attachments, msg.guild)
        if await self.message_handler.is_pass_through(temp_msg):
            if is_en:
                await self.message_handler.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], raw, msg, lang="Chinese")
            else:
                await self.message_handler.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], raw, msg, lang="English")
            return
        txt = strip_banner(raw)
        lang = await self.gpt_handler.detect_language(txt)
        
        reply_context = None
        ref = await self.message_handler._get_ref_message(msg)
        if ref is not None:
            reply_context = strip_banner(ref.content or "")
        
        async def to_target(text: str, direction: str) -> str:
            tr = await self.translator.translate_text(text, direction, cm, context=reply_context)
            if tr == "/":
                return text
            return tr
        if is_en:
            if lang == "English":
                tr = await to_target(txt, "en_to_zh")
                await self.message_handler.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], tr, msg, lang="Chinese")
            elif lang == "Chinese":
                await self.message_handler.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")
                tr = await to_target(txt, "zh_to_en")
                await self.message_handler.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
            else:
                await self.message_handler.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")
        else:
            if lang == "Chinese":
                tr = await to_target(txt, "zh_to_en")
                await self.message_handler.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
            elif lang == "English":
                await self.message_handler.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], txt, msg, lang="English")
                zh_tr = await to_target(txt, "en_to_zh")
                await self.message_handler.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], zh_tr, msg, lang="Chinese")
            else:
                await self.message_handler.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], txt, msg, lang="English")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.webhook_id or not after.guild:
            return
        gid = after.guild.id
        neighbors = self.mirror_manager.get_neighbors(gid, after.id)
        if not neighbors:
            return
        txt = strip_banner(after.content or "")
        for ch_id, mid in list(neighbors.items()):
            try:
                ch = after.guild.get_channel(ch_id) or await self.fetch_channel(ch_id)
                old = await ch.fetch_message(mid)
                try:
                    await old.delete()
                except Exception:
                    pass
            except Exception:
                continue
        cfg = self._guild_cfg(str(gid))
        if not cfg:
            return
        if after.channel.id in [cfg["en_channel_id"], cfg["zh_channel_id"]]:
            await self.on_message(after)

    async def on_message_delete(self, msg: discord.Message):
        if msg.author.bot or msg.webhook_id or not msg.guild:
            return
        gid = msg.guild.id
        neighbors = self.mirror_manager.get_neighbors(gid, msg.id)
        for ch_id, mid in list(neighbors.items()):
            try:
                ch = msg.guild.get_channel(ch_id) or await self.fetch_channel(ch_id)
                m = await ch.fetch_message(mid)
                await m.delete()
            except Exception:
                continue

def main():
    if not config.get("discord_token"):
        raise RuntimeError("Discord Bot Token not found. Set DISCORD_TOKEN environment variable or add to config.json")
    if not config.get("openai_key"):
        raise RuntimeError("OpenAI API Key not found. Set OPENAI_KEY environment variable or add to config.json")
    
    logger.info("Starting Discord Translator Bot...")
    logger.info(f"Bot will run on {len(config.get('guilds', {}))} configured guilds")
    bot = TranslatorBot()
    prompt_mod.register_commands(
        bot=bot,
        config=config,
        guild_dicts=guild_dicts,
        dictionary_path=DICTIONARY_PATH,
        guild_abbrs=guild_abbrs,
        abbr_path=ABBREV_PATH,
        can_use=lambda g, m: bot.is_admin_user(g, m),
    )
    print("bot running")
    bot.run(config["discord_token"])

if __name__ == "__main__":
    main()
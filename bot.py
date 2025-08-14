#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import re
from typing import Optional, Tuple, List, Dict
from io import BytesIO
from collections import deque

import aiohttp
import discord
from discord.ext import commands, tasks
from openai import AsyncOpenAI
import deepl
from PIL import Image
import pytesseract
from dotenv import load_dotenv

from preprocess import preprocess, FSURE_HEAD, FSURE_SEP
import joy_cmds as prompt_mod
import health_server
from storage import storage

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE, "config.json")
DICTIONARY_PATH = os.path.join(BASE, "dictionary.json")
ABBREV_PATH = os.path.join(BASE, "abbreviations.json")
PASSTHROUGH_PATH = os.path.join(BASE, "passthrough.json")

def _load_json_or(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else fallback
    except Exception:
        return fallback

config = _load_json_or(CONFIG_PATH, {})

# 优先使用环境变量，回退到配置文件
config["discord_token"] = os.getenv("DISCORD_TOKEN", config.get("discord_token", ""))
config["openai_key"] = os.getenv("OPENAI_KEY", os.getenv("OPENAI_API_KEY", config.get("openai_key", "")))
config["deepl_key"] = "adef608f-1d8b-4831-94a2-37a6992c77d8:fx"

# 初始化 OpenAI 客户端 (仍用于判断功能)
openai_client = AsyncOpenAI(api_key=config["openai_key"]) if config.get("openai_key") else None

# 初始化 DeepL 客户端 (用于翻译功能)
deepl_client = deepl.Translator(config["deepl_key"])

# 启动时打一条掩码日志，确认进程里确实拿到了 key
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
# These will be loaded asynchronously in setup_hook
guild_dicts = {}
guild_abbrs = {"default": {}}
passthrough_cfg = {"default": {"commands": [], "fillers": []}}

REPLY_ICON_DEFAULT = config.get("reply_icon", "↪")
REPLY_LABEL_EN = "REPLY"
REPLY_LABEL_ZH = "回复"
MIRROR_PATH = os.path.join(BASE, config.get("mirror_store_path", "mirror.json"))
MIRROR_MAX_PER_GUILD = int(config.get("mirror_prune_max_per_guild", 4000))
PREVIEW_LIMIT = int(config.get("reply_preview_limit", 90))
REPLY_PREVIEW_LIMIT = int(config.get("reply_preview_limit_reply", 50))

URL_RE = re.compile(r"https?://\S+")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,}:\d+>")
UNICODE_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U00002600-\U000026FF\U0001F1E6-\U0001F1FF]+")
PUNCT_GAP_RE = re.compile(r"[\s\W_]+", re.UNICODE)
OCR_NOTE_RE = re.compile(r"^(?:Image text translation:)", re.I)

def build_jump_url(gid: int, cid: int, mid: int) -> str:
    return f"https://discord.com/channels/{gid}/{cid}/{mid}"

def is_image_attachment(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return any(name.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))

def strip_banner(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith(">"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = [ln for ln in lines[i:] if not OCR_NOTE_RE.match(ln.strip())]
    return "\n".join(body).strip()

def _coerce_int_keys(obj):
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            try:
                ik = int(k)
            except (ValueError, TypeError):
                ik = k
            new[ik] = _coerce_int_keys(v)
        return new
    if isinstance(obj, list):
        return [_coerce_int_keys(x) for x in obj]
    return obj

def _merge_default(mapping: Dict, gid: str) -> Dict:
    base = mapping.get("default", {})
    out = dict(base)
    out.update(mapping.get(gid, {}))
    return out

def _normalize_wrapped_urls(s: str) -> str:
    if not s:
        return s
    return re.sub(r"<+\s*(https?://[^>\s]+)\s*>+", r"<\1>", s)

def _suppress_url_embeds(s: str) -> str:
    def _wrap(m: re.Match) -> str:
        u = m.group(0)
        if u.startswith("<") and u.endswith(">"):
            return u
        return f"<{u}>"
    return URL_RE.sub(_wrap, s or "")

def _shorten(s: str, n: int) -> str:
    if n and n > 0 and len(s) > n:
        return s[: n - 1].rstrip() + "…"
    return s

def _delink_for_reply(s: str) -> str:
    if not s:
        return s
    s = _normalize_wrapped_urls(s)
    s = re.sub(r"<\s*(https?://[^>\s]+)\s*>", r"\1", s)
    s = re.sub(r"(?i)\bhttps?://", lambda m: m.group(0)[0] + "\u200b" + m.group(0)[1:], s)
    s = re.sub(r"(?i)\bwww\.", "w\u200bbw.", s)
    return s

def _is_command_text(gid: str, s: str) -> bool:
    cmds = _merge_default(passthrough_cfg, gid).get("commands", [])
    if not s:
        return False
    t = s.strip()
    for c in cmds:
        if t.lower().startswith(c.lower()):
            return True
    return False

def _is_filler(s: str, gid: str) -> bool:
    if not s:
        return False
    base = _merge_default(passthrough_cfg, gid).get("fillers", [])
    t = CUSTOM_EMOJI_RE.sub("", s)
    t = UNICODE_EMOJI_RE.sub("", t)
    t = t.strip().lower()
    if not t:
        return True
    if any(t == f.lower() for f in base):
        return True
    if re.fullmatch(r"(e?hm+|e+m+h+|em+|oh+|ah+|uh+h*|h+|w+|…+|\.)", t):
        return True
    return False

def _apply_dictionary(text: str, direction: str, custom_map: dict) -> str:
    s = text or ""
    if not custom_map:
        return s
    if direction == "zh_to_en":
        for zh, en in sorted(custom_map.items(), key=lambda kv: len(kv[0]), reverse=True):
            s = s.replace(zh, en)
    else:
        inv = {v: k for k, v in custom_map.items()}
        for en, zh in sorted(inv.items(), key=lambda kv: len(kv[0]), reverse=True):
            pat = re.compile(rf"\b{re.escape(en)}\b", re.IGNORECASE)
            s = pat.sub(zh, s)
    return s

def _apply_abbreviations(text: str, gid: str) -> str:
    d = _merge_default(guild_abbrs, gid)
    if not d:
        return text or ""
    s = text or ""

    def is_url_context(idx: int) -> bool:
        left = max(0, idx - 8)
        right = min(len(s), idx + 16)
        seg = s[left:right]
        return bool(URL_RE.search(seg))

    for k, v in sorted(d.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not k:
            continue
        end_zh = k.endswith("的") or v.endswith("的") or bool(re.search(r"[\u4e00-\u9fff]", k + v))
        if end_zh:
            pat = re.compile(re.escape(k))
            def rep(m: re.Match):
                i = m.end()
                if i < len(s):
                    nxt = s[i]
                    if URL_RE.match(s[i:]):
                        return m.group(0)
                    if re.match(r"[A-Za-z0-9_]", nxt):
                        return m.group(0)
                return v
            s = pat.sub(rep, s)
        else:
            pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(k)}(?![A-Za-z0-9_])")
            def rep2(m: re.Match):
                return v if not is_url_context(m.start()) else m.group(0)
            s = pat.sub(rep2, s)
    return s

class TranslatorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.openai_client = openai_client
        self.session: Optional[aiohttp.ClientSession] = None
        self.no_ping = discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=False)
        self.mirror_map: Dict[int, Dict[int, Dict[int, int]]] = {}
        self._recent_user_message: Dict[int, int] = {}
        self.health_runner = None

    def _mirror_load(self):
        try:
            if os.path.exists(MIRROR_PATH):
                with open(MIRROR_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.mirror_map = _coerce_int_keys(data) or {}
                logger.info("Loaded mirror map from %s (%d guilds)", MIRROR_PATH, len(self.mirror_map))
        except Exception as e:
            logger.exception("Load mirror_map failed: %s", e)
            self.mirror_map = {}

    def _mirror_save(self):
        try:
            with open(MIRROR_PATH, "w", encoding="utf-8") as f:
                json.dump(self.mirror_map, f, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            logger.exception("Save mirror_map failed: %s", e)

    def _mirror_prune(self, gid: int):
        if MIRROR_MAX_PER_GUILD <= 0:
            return
        g = self.mirror_map.setdefault(gid, {})
        over = max(0, len(g) - MIRROR_MAX_PER_GUILD)
        if over <= 0:
            return
        for _ in range(over):
            try:
                k = next(iter(g))
            except StopIteration:
                break
            g.pop(k, None)

    async def setup_hook(self):
        global guild_dicts, guild_abbrs, passthrough_cfg
        
        # Load persistent data
        logger.info("Loading persistent data...")
        guild_dicts.update(await storage.load_json("dictionary", {}))
        
        # Hardcoded default abbreviations as fallback
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
        
        # Load abbreviations from local file first (for defaults), then merge with cloud data
        logger.info(f"Attempting to load abbreviations from: {ABBREV_PATH}")
        logger.info(f"File exists: {os.path.exists(ABBREV_PATH)}")
        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"BASE directory: {BASE}")
        
        local_abbrs = _load_json_or(ABBREV_PATH, hardcoded_defaults)
        cloud_abbrs = await storage.load_json("abbreviations", {})
        
        # Debug logging
        logger.info(f"Local abbreviations loaded: {len(local_abbrs)} groups")
        logger.info(f"Local default abbreviations: {len(local_abbrs.get('default', {}))}")
        logger.info(f"Cloud abbreviations loaded: {len(cloud_abbrs)} groups")
        
        # Merge: start with local defaults, then add cloud data
        guild_abbrs.clear()
        guild_abbrs.update(local_abbrs)
        
        # Merge cloud data into local data (cloud data for specific guilds can override)
        for guild_id, abbr_data in cloud_abbrs.items():
            if guild_id == "default":
                # For default, merge instead of replace to keep local defaults
                guild_abbrs["default"].update(abbr_data)
            else:
                # For guild-specific data, use cloud version
                guild_abbrs[guild_id] = abbr_data
        
        # Load passthrough from local file only (not from cloud storage)
        passthrough_cfg.update(_load_json_or(PASSTHROUGH_PATH, {"default": {"commands": [], "fillers": []}}))
        
        logger.info(f"Loaded {len(guild_dicts)} guilds in dictionary")
        default_abbr_count = len(guild_abbrs.get("default", {}))
        logger.info(f"Final result: {len(guild_abbrs)} abbreviation groups with {default_abbr_count} default abbreviations")
        logger.info(f"Default abbreviations sample: {list(guild_abbrs.get('default', {}).keys())[:10]}")
        
        self._mirror_load()
        self.session = aiohttp.ClientSession()
        # Start health check server
        self.health_runner = await health_server.start_health_server()
        # Start heartbeat task
        self.heartbeat_task.start()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        self._mirror_save()
        # Stop heartbeat task
        self.heartbeat_task.cancel()
        # Stop health server
        if self.health_runner:
            await self.health_runner.cleanup()
        await super().close()
    
    @tasks.loop(seconds=30)
    async def heartbeat_task(self):
        """Send heartbeat to health server"""
        health_server.update_bot_status(running=True)
    
    @heartbeat_task.before_loop
    async def before_heartbeat(self):
        await self.wait_until_ready()

    def _mirror_add(self, gid: int, src_id: int, ch_id: int, mapped_id: int):
        self.mirror_map.setdefault(gid, {}).setdefault(src_id, {})[ch_id] = mapped_id
        self._mirror_prune(gid)
        self._mirror_save()

    def _mirror_neighbors(self, gid: int, src_id: int) -> Dict[int, int]:
        return self.mirror_map.get(gid, {}).get(src_id, {})

    def _find_mirror_id(self, gid: int, src_msg_id: int, target_channel_id: int) -> Optional[int]:
        if gid not in self.mirror_map or src_msg_id not in self.mirror_map[gid]:
            return None
        visited = set([src_msg_id])
        q = deque([src_msg_id])
        while q:
            cur = q.popleft()
            neighbors: Dict[int, int] = self.mirror_map[gid].get(cur, {})
            if target_channel_id in neighbors:
                return neighbors[target_channel_id]
            for nxt in neighbors.values():
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        return None

    async def _fetch_message(self, guild: discord.Guild, channel_id: int, message_id: int) -> Optional[discord.Message]:
        ch = self.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.fetch_channel(channel_id)
            except Exception:
                return None
        try:
            return await ch.fetch_message(message_id)
        except Exception:
            return None

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

    async def detect_language(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return "meaningless"
        
        # First try simple character counting for obvious cases
        t2 = CUSTOM_EMOJI_RE.sub("", t)
        t2 = UNICODE_EMOJI_RE.sub("", t2)
        t2 = re.sub(r"(e?m+)+", "em", t2, flags=re.IGNORECASE)
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
        en_count = len(re.findall(r"[A-Za-z]", t2))
        
        # Pure single language cases
        if zh_count and not en_count:
            return "Chinese"
        if en_count and not zh_count:
            return "English"
        
        # Mixed language cases - use AI to determine primary language
        if zh_count and en_count:
            logger.info(f"DEBUG: Mixed language detected: zh={zh_count}, en={en_count}")
            return await self._ai_detect_language(t)
        
        return "meaningless"

    async def _ai_detect_language(self, text: str) -> str:
        """Use AI to detect primary language for mixed-language text"""
        sys = (
            "Analyze the text and determine the PRIMARY language. "
            "Consider which language carries the main meaning. "
            "Output exactly one word: Chinese, English, or meaningless."
        )
        usr = f"Text: {text}"
        try:
            if not self.openai_client:
                # Fallback to character counting
                t2 = CUSTOM_EMOJI_RE.sub("", text)
                zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
                en_count = len(re.findall(r"[A-Za-z]", t2))
                return "Chinese" if zh_count >= en_count else "English"
                
            r = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
                max_tokens=5, 
                temperature=0.0
            )
            result = (r.choices[0].message.content or "").strip().lower()
            if "chinese" in result:
                return "Chinese"
            if "english" in result:
                return "English"
            # Default fallback
            t2 = CUSTOM_EMOJI_RE.sub("", text)
            zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
            en_count = len(re.findall(r"[A-Za-z]", t2))
            return "Chinese" if zh_count >= en_count else "English"
        except Exception as e:
            logger.error(f"AI language detection failed: {e}")
            # Fallback to character counting
            t2 = CUSTOM_EMOJI_RE.sub("", text)
            zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
            en_count = len(re.findall(r"[A-Za-z]", t2))
            return "Chinese" if zh_count >= en_count else "English"


    async def is_profanity(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        try:
            if not self.openai_client:
                return False
            mr = await self.openai_client.moderations.create(model="text-moderation-latest", input=t)
            if mr and mr.results:
                return bool(mr.results[0].flagged)
        except Exception as e:
            logger.error(f"OpenAI moderation failed: {e}")
            pass
        try:
            if not self.openai_client:
                return False
            sys = "Classify if the text contains profanity or swear words. Reply with exactly one token: PROFANE or CLEAN."
            usr = f"<text>{t}</text>"
            r = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
                max_tokens=1, temperature=0.0
            )
            return "profane" in (r.choices[0].message.content or "").lower()
        except Exception:
            return False

    async def _apply_star_patch(self, prev_text: str, patch: str) -> str:
        lang = await self.detect_language(prev_text)
        logger.info(f"DEBUG: Star patch - lang: {lang}, prev: '{prev_text}', patch: '{patch}'")
        
        if lang == "Chinese":
            sys = (
                "用户发送了两条消息：第一条是完整句子，第二条以*结尾是补丁。"
                "你需要将补丁内容智能地合并到原句中，形成一个完整的新句子。"
                "规则：\n"
                "1. 如果补丁是替换词，就替换原句中最相关的部分\n"
                "2. 如果补丁是补充词，就添加到原句合适的位置\n"
                "3. 保持语法正确和语义连贯\n"
                "4. 只返回合并后的完整句子，不要解释"
            )
            usr = f"原句：{prev_text}\n补丁：{patch}\n\n请返回合并后的句子："
        else:
            sys = (
                "User sent two messages: first is a complete sentence, second ends with * as a patch. "
                "You need to intelligently merge the patch content into the original sentence to form one complete new sentence.\n"
                "Rules:\n"
                "1. If patch is a replacement word, replace the most relevant part in original\n"
                "2. If patch is additional word, add it to appropriate position in original\n"
                "3. Keep grammar correct and meaning coherent\n"
                "4. Return only the merged complete sentence, no explanation"
            )
            usr = f"ORIGINAL: {prev_text}\nPATCH: {patch}\n\nReturn merged sentence:"
        
        try:
            if not self.openai_client:
                logger.info(f"DEBUG: No OpenAI client, using fallback")
                # Simple fallback: append patch to original
                return f"{prev_text} {patch}".strip()
            
            logger.info(f"DEBUG: Calling OpenAI for star patch merge...")
            r = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
                temperature=0.0
            )
            logger.info(f"DEBUG: OpenAI response received")
            result = (r.choices[0].message.content or "").strip()
            logger.info(f"DEBUG: Star patch result: '{result}'")
            return result or prev_text
        except Exception as e:
            logger.error(f"OpenAI star patch failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Fallback: simple append
            fallback_result = f"{prev_text} {patch}".strip()
            logger.info(f"DEBUG: Using fallback result: '{fallback_result}'")
            return fallback_result

    async def _call_translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        logger.info(f"DEBUG: _call_translate: '{src_text}' from {src_lang} to {tgt_lang}")
        if not src_text:
            logger.info("DEBUG: Empty src_text, returning /")
            return "/"
        
        try:
            # Map language names to DeepL language codes
            if src_lang == "Chinese":
                source_lang = "ZH"
            elif src_lang == "English":
                source_lang = "EN"
            else:
                source_lang = None  # Let DeepL auto-detect
            
            if tgt_lang.startswith("Chinese"):
                target_lang = "ZH"
            elif tgt_lang == "English":
                target_lang = "EN-US"
            else:
                logger.error(f"Unsupported target language: {tgt_lang}")
                return "/"
            
            logger.info(f"DEBUG: Calling DeepL API with source={source_lang}, target={target_lang}")
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: deepl_client.translate_text(src_text, target_lang=target_lang, source_lang=source_lang)
            )
            out = result.text.strip()
            logger.info(f"DEBUG: DeepL returned: '{out}'")
            return out or "/"
        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            return "/"

    async def translate_text(self, text: str, direction: str, custom_map: dict) -> str:
        logger.info(f"DEBUG: translate_text called with '{text}', direction='{direction}'")
        if direction == "zh_to_en":
            pre = preprocess(_apply_dictionary(text, "zh_to_en", custom_map), "zh_to_en")
            logger.info(f"DEBUG: After dictionary+preprocess: '{pre}'")
            if pre.startswith(FSURE_HEAD):
                payload = pre[len(FSURE_HEAD):]
                if FSURE_SEP in payload:
                    core, tail = payload.split(FSURE_SEP, 1)
                else:
                    core, tail = payload, ""
                en_core = await self._call_translate(core, "Chinese", "English")
                en_tail = await self._call_translate(tail, "Chinese", "English") if tail.strip() else ""
                out = (en_core or "/")
                if out != "/":
                    out = out.strip().rstrip(".") + " for sure"
                    if en_tail and en_tail != "/":
                        out = out + ", " + en_tail
                return out or "/"
            result = await self._call_translate(pre, "Chinese", "English")
            logger.info(f"DEBUG: Translation result: '{result}'")
            return result
        else:
            pre = preprocess(_apply_dictionary(text, "en_to_zh", custom_map), "en_to_zh")
            result = await self._call_translate(pre, "English", "Chinese (Simplified)")
            logger.info(f"DEBUG: Translation result: '{result}'")
            return result

    def _text_after_abbrev_pre(self, s: str, gid: str) -> str:
        return _apply_abbreviations(s or "", gid)

    async def is_pass_through(self, msg: discord.Message) -> bool:
        t = (msg.content or "")
        t2 = CUSTOM_EMOJI_RE.sub("", t)
        t2 = UNICODE_EMOJI_RE.sub("", t2)
        t2 = PUNCT_GAP_RE.sub("", t2)
        if not t2 and not msg.attachments:
            return True
        if URL_RE.fullmatch(t.strip()):
            return True
        gid = str(msg.guild.id)
        if _is_command_text(gid, msg.content):
            return True
        if _is_filler(msg.content, gid):
            return True
        return not re.search(r"[A-Za-z\u4e00-\u9fff]", t2)

    async def _choose_jump_and_preview(self, ref: discord.Message, target_lang: str, target_channel_id: int) -> tuple[str, str, bool]:
        gid = ref.guild.id if ref.guild else 0
        if ref.channel.id == target_channel_id:
            show_text = strip_banner(ref.content or "")
            only_image = (not show_text) and any(is_image_attachment(a) for a in ref.attachments)
            jump = build_jump_url(gid, ref.channel.id, ref.id)
            return jump, show_text, only_image
        mirror_id = self._find_mirror_id(gid, ref.id, target_channel_id)
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
        cm = guild_dicts.get(gid_str, {})
        ref_lang = await self.detect_language(raw)
        if target_lang == "Chinese" and ref_lang == "English":
            show = await self.translate_text(raw, "en_to_zh", cm)
        elif target_lang == "English" and ref_lang == "Chinese":
            show = await self.translate_text(raw, "zh_to_en", cm)
        else:
            show = raw
        return jump, show, False

    async def _make_top_reply_banner(self, ref: discord.Message, target_lang: str, target_channel_id: int) -> str:
        reply_label = REPLY_LABEL_ZH if target_lang == "Chinese" else REPLY_LABEL_EN
        reply_icon = REPLY_ICON_DEFAULT
        jump, preview, only_image = await self._choose_jump_and_preview(ref, target_lang, target_channel_id)
        if only_image:
            preview = "[image]"
        preview = re.sub(r"\s+", " ", preview).strip()
        preview = _delink_for_reply(preview)
        preview = _shorten(preview, REPLY_PREVIEW_LIMIT)
        return f"> {ref.author.mention} {reply_icon} [{reply_label}]({jump}) {preview}".rstrip()

    async def send_via_webhook(self, webhook_url: str, target_channel_id: int, content: str, msg: discord.Message, *, lang: str):
        if not self.session:
            raise RuntimeError("HTTP session not initialized")
        wh = discord.Webhook.from_url(webhook_url, session=self.session)

        files_data: List[Tuple[str, bytes]] = []
        for att in msg.attachments:
            try:
                data = await att.read()
                files_data.append((att.filename, data))
            except Exception:
                logger.exception("read attachment failed")

        ocr_lines: List[str] = []
        for fn, data in files_data:
            try:
                img = Image.open(BytesIO(data))
                text = pytesseract.image_to_string(img, lang="chi_sim+eng").strip()
                if not text:
                    continue
                gid_str = str(msg.guild.id)
                cm = guild_dicts.get(gid_str, {})
                if await self.is_profanity(text):
                    # Return appropriate swear message based on target language
                    ocr_tr = "（脏话）" if lang == "Chinese" else "(swearing)"
                else:
                    ocr_lang = await self.detect_language(text)
                    if ocr_lang == "Chinese":
                        ocr_tr = await self.translate_text(text, "zh_to_en", cm)
                    elif ocr_lang == "English":
                        ocr_tr = await self.translate_text(text, "en_to_zh", cm)
                    else:
                        ocr_tr = ""
                if ocr_tr and ocr_tr != "/":
                    ocr_lines.append("Image text translation: " + ocr_tr)
            except Exception:
                logger.exception("OCR failed for an attachment")

        top_banner = ""
        ref = await self._get_ref_message(msg)
        if ref is not None:
            try:
                top_banner = await self._make_top_reply_banner(ref, lang, target_channel_id)
            except Exception:
                logger.exception("build top reply banner failed")

        body = (content or "").strip()
        body = _suppress_url_embeds(body)

        final_lines: List[str] = []
        if top_banner:
            final_lines.append(top_banner)
        if body:
            final_lines.append(body)
        if ocr_lines:
            final_lines.extend(ocr_lines)
        final = "\n".join(final_lines)

        try:
            sent = await wh.send(
                content=final or None,
                username=msg.author.display_name,
                avatar_url=(msg.author.avatar.url if msg.author.avatar else None),
                files=[discord.File(fp=BytesIO(d), filename=fn) for fn, d in files_data] or [],
                allowed_mentions=self.no_ping,
                wait=True,
            )
            try:
                if isinstance(sent, (discord.Message, discord.WebhookMessage)):
                    logger.info(f"DEBUG: Adding mirror mapping: user_msg {msg.id} -> translated_msg {sent.id} in channel {target_channel_id}")
                    self._mirror_add(msg.guild.id, msg.id, target_channel_id, int(sent.id))
                    self._mirror_add(msg.guild.id, int(sent.id), msg.channel.id, msg.id)
            except Exception:
                logger.exception("mirror map save failed")
        except Exception:
            logger.exception("Webhook send failed")

    async def _process_star_patch_if_any_with_content(self, content: str, msg: discord.Message) -> Optional[Tuple[str, int]]:
        """Process star patch using provided content instead of msg.content"""
        t = content.strip()
        
        # Check if it's a potential star patch: ends with * and no newlines
        if len(t) >= 2 and t.endswith("*") and "\n" not in t:
            # Avoid treating markdown formatting as patches
            # Skip if it looks like *italic*, **bold**, ***bold-italic***
            if t.startswith("*") or t.startswith("**") or t.startswith("***"):
                logger.info(f"DEBUG: Skipping markdown format: '{t}'")
                return None
                
            # Skip if it contains balanced markdown (e.g., "text *word* more*")  
            # Count * occurrences - if even number, likely markdown pairs
            star_count = t.count("*")
            if star_count > 1 and star_count % 2 == 0:
                # Check if there are matching * pairs before the final *
                inner_text = t[:-1]  # Remove the final *
                if "*" in inner_text:
                    logger.info(f"DEBUG: Skipping potential markdown pairs: '{t}'")
                    return None
            
            logger.info(f"DEBUG: Processing star patch: '{t}'")
            ref = await self._get_ref_message(msg)
            base = None
            if ref and ref.author.id == msg.author.id:
                base = ref.content or ""
            else:
                last_id = self._recent_user_message.get(msg.author.id)
                if last_id:
                    try:
                        base_msg = await msg.channel.fetch_message(last_id)
                        if base_msg and base_msg.author.id == msg.author.id:
                            base = base_msg.content or ""
                    except Exception:
                        base = None
            if base:
                patch_text = t[:-1].strip()  # Remove the trailing * and strip whitespace
                base_text = base.strip()
                
                # Additional validation for valid patches
                # 1. Patch content should not be empty
                if not patch_text:
                    logger.info(f"DEBUG: Skipping empty patch: '{t}'")
                    return None
                    
                # 2. Base message should not also end with * (avoid patch chains)
                if base_text.endswith("*"):
                    logger.info(f"DEBUG: Skipping patch on patch: base '{base_text}' also ends with *")
                    return None
                    
                # 3. Patch and base should be different
                if patch_text == base_text:
                    logger.info(f"DEBUG: Skipping identical patch: '{patch_text}' same as base")
                    return None
                
                logger.info(f"DEBUG: Applying patch '{patch_text}' to base '{base_text}'")
                try:
                    fixed = await self._apply_star_patch(strip_banner(base_text), patch_text)
                    logger.info(f"DEBUG: Patch result received: '{fixed}'")
                    if fixed and fixed.strip():
                        logger.info(f"DEBUG: Returning valid patch result with original msg ID: '{fixed}', {last_id}")
                        return (fixed, last_id)  # Return both patched content and original message ID
                    else:
                        logger.error(f"DEBUG: Patch result is empty or None, returning None")
                        return None
                except Exception as e:
                    logger.error(f"DEBUG: Exception in _apply_star_patch: {e}")
                    return None
            else:
                logger.info(f"DEBUG: No base message found for star patch")
        return None

    async def _process_star_patch_if_any(self, msg: discord.Message) -> Optional[str]:
        t = (msg.content or "").strip()
        
        # Check if it's a potential star patch: ends with * and no newlines
        if len(t) >= 2 and t.endswith("*") and "\n" not in t:
            # Avoid treating markdown formatting as patches
            # Skip if it looks like *italic*, **bold**, ***bold-italic***
            if t.startswith("*") or t.startswith("**") or t.startswith("***"):
                logger.info(f"DEBUG: Skipping markdown format: '{t}'")
                return None
                
            # Skip if it contains balanced markdown (e.g., "text *word* more*")  
            # Count * occurrences - if even number, likely markdown pairs
            star_count = t.count("*")
            if star_count > 1 and star_count % 2 == 0:
                # Check if there are matching * pairs before the final *
                inner_text = t[:-1]  # Remove the final *
                if "*" in inner_text:
                    logger.info(f"DEBUG: Skipping potential markdown pairs: '{t}'")
                    return None
            
            logger.info(f"DEBUG: Processing star patch: '{t}'")
            ref = await self._get_ref_message(msg)
            base = None
            if ref and ref.author.id == msg.author.id:
                base = ref.content or ""
            else:
                last_id = self._recent_user_message.get(msg.author.id)
                if last_id:
                    try:
                        base_msg = await msg.channel.fetch_message(last_id)
                        if base_msg and base_msg.author.id == msg.author.id:
                            base = base_msg.content or ""
                    except Exception:
                        base = None
            if base:
                patch_text = t[:-1].strip()  # Remove the trailing * and strip whitespace
                base_text = base.strip()
                
                # Additional validation for valid patches
                # 1. Patch content should not be empty
                if not patch_text:
                    logger.info(f"DEBUG: Skipping empty patch: '{t}'")
                    return None
                    
                # 2. Base message should not also end with * (avoid patch chains)
                if base_text.endswith("*"):
                    logger.info(f"DEBUG: Skipping patch on patch: base '{base_text}' also ends with *")
                    return None
                    
                # 3. Patch and base should be different
                if patch_text == base_text:
                    logger.info(f"DEBUG: Skipping identical patch: '{patch_text}' same as base")
                    return None
                
                logger.info(f"DEBUG: Applying patch '{patch_text}' to base '{base_text}'")
                try:
                    fixed = await self._apply_star_patch(strip_banner(base_text), patch_text)
                    logger.info(f"DEBUG: Patch result received: '{fixed}'")
                    if fixed and fixed.strip():
                        logger.info(f"DEBUG: Returning valid patch result with original msg ID: '{fixed}', {last_id}")
                        return (fixed, last_id)  # Return both patched content and original message ID
                    else:
                        logger.error(f"DEBUG: Patch result is empty or None, returning None")
                        return None
                except Exception as e:
                    logger.error(f"DEBUG: Exception in _apply_star_patch: {e}")
                    return None
            else:
                logger.info(f"DEBUG: No base message found for star patch")
        return None

    async def _handle_star_patch_edit(self, processed_content: str, msg: discord.Message, cfg: dict, gid: str, cm: dict, original_msg_id: int):
        """Handle star patch by editing existing translated messages instead of sending new ones"""
        logger.info(f"DEBUG: Handling star patch edit for content: '{processed_content}'")
        
        # Use the original message ID passed from the patch processing
        last_id = original_msg_id
        logger.info(f"DEBUG: Using original message ID from patch processing: {last_id}")
        if not last_id:
            logger.info("DEBUG: No original message ID provided for star patch edit")
            return
            
        logger.info(f"DEBUG: Looking for mirrors of original message {last_id}")
        logger.info(f"DEBUG: Current mirror_map has {len(self.mirror_map.get(msg.guild.id, {}))} entries for this guild")
        
        # Debug: show full mirror_map for this guild
        gid_int = msg.guild.id
        guild_mirrors = self.mirror_map.get(gid_int, {})
        logger.info(f"DEBUG: Full mirror_map for guild {gid_int}: {guild_mirrors}")
            
        try:
            # Find the mirror messages for the original message
            neighbors = self._mirror_neighbors(gid_int, last_id)
            if not neighbors:
                logger.info(f"DEBUG: No mirror messages found for original message {last_id}")
                logger.info(f"DEBUG: Available message IDs in mirror_map: {list(guild_mirrors.keys())}")
                
                # Check if any of the available IDs might be the right one
                for msg_id, channels in guild_mirrors.items():
                    logger.info(f"DEBUG: Message {msg_id} maps to channels: {channels}")
                return
            
            logger.info(f"DEBUG: Found {len(neighbors)} mirror messages for original message {last_id}: {neighbors}")
                
            txt = strip_banner(processed_content)
            lang = await self.detect_language(txt)
            logger.info(f"DEBUG: Star patch detected language: '{lang}' for text: '{txt}'")
            
            async def to_target(text: str, direction: str) -> str:
                tr = await self.translate_text(text, direction, cm)
                if tr == "/":
                    return text
                return tr
            
            # Edit messages in target channels
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
                        # From ZH channel, edit EN channel message  
                        logger.info(f"DEBUG: Editing EN channel message from ZH channel")
                        if lang == "Chinese":
                            new_content = await to_target(txt, "zh_to_en")
                        elif lang == "English":
                            new_content = txt
                        else:
                            new_content = txt
                            
                    elif is_en and ch_id == cfg["zh_channel_id"]:
                        # From EN channel, edit ZH channel message
                        logger.info(f"DEBUG: Editing ZH channel message from EN channel")
                        if lang == "English":
                            new_content = await to_target(txt, "en_to_zh")
                        elif lang == "Chinese":
                            new_content = txt
                        else:
                            new_content = txt
                    
                    if new_content:
                        logger.info(f"DEBUG: Attempting to edit message to: '{new_content}'")
                        
                        # Check if this is a webhook message
                        if mirror_msg.webhook_id:
                            logger.info(f"DEBUG: Editing webhook message via webhook")
                            # For webhook messages, we need to use the webhook to edit
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
                            # Regular bot message
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
        if _is_command_text(gid, msg.content):
            return
        
        # Check for star patch FIRST (before updating recent message ID)
        original_content = msg.content or ""
        logger.info(f"DEBUG: Checking for star patch in message: '{original_content}'")
        patch_result = await self._process_star_patch_if_any_with_content(original_content, msg)
        if patch_result is not None:
            patched_content, original_msg_id = patch_result
            logger.info(f"DEBUG: Star patch detected! Patched content: '{patched_content}', original msg ID: {original_msg_id}")
        else:
            patched_content, original_msg_id = None, None
            logger.info(f"DEBUG: No star patch detected")
        
        # Update recent message ID only after patch check
        old_id = self._recent_user_message.get(msg.author.id)
        self._recent_user_message[msg.author.id] = msg.id
        logger.info(f"DEBUG: Updated _recent_user_message for user {msg.author.id}: {old_id} -> {msg.id}")
        
        cm = guild_dicts.get(gid, {})
        raw = msg.content or ""
        logger.info(f"DEBUG: Original message: '{raw}'")
        # Apply preprocessing first (handles 6/666 -> 厉害 conversion)
        from preprocess import preprocess
        raw = preprocess(raw, "zh_to_en")  # Always use zh_to_en for praise number conversion
        logger.info(f"DEBUG: After preprocessing: '{raw}'")
        raw = self._text_after_abbrev_pre(raw, gid)
        logger.info(f"DEBUG: After abbreviations: '{raw}'")
        
        if patched_content is not None:
            logger.info(f"DEBUG: Star patch applied, using patched content: '{patched_content}'")
            # Apply preprocessing and abbreviations to the patched result
            raw = preprocess(patched_content, "zh_to_en")
            raw = self._text_after_abbrev_pre(raw, gid)
            logger.info(f"DEBUG: Patched content after processing: '{raw}'")
            
            # For star patches, edit existing messages instead of sending new ones
            await self._handle_star_patch_edit(raw, msg, cfg, gid, cm, original_msg_id)
            return
        
        # Check pass-through using processed text (after potential star patch)
        temp_msg = msg  # Create a temporary message object with processed content
        temp_msg.content = raw
        if await self.is_pass_through(temp_msg):
            logger.info(f"DEBUG: Message '{raw}' marked as pass-through")
            if is_en:
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], raw, msg, lang="Chinese")
            else:
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], raw, msg, lang="English")
            return
        logger.info(f"DEBUG: Message '{raw}' will go through translation")
        txt = strip_banner(raw)
        lang = await self.detect_language(txt)
        logger.info(f"DEBUG: Detected language: '{lang}' for text: '{txt}'")
        async def to_target(text: str, direction: str) -> str:
            tr = await self.translate_text(text, direction, cm)
            if tr == "/":
                return text
            return tr
        if is_en:
            logger.info(f"DEBUG: In English channel, detected language: {lang}")
            if lang == "English":
                logger.info(f"DEBUG: English in EN channel - translating to Chinese")
                tr = await to_target(txt, "en_to_zh")
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], tr, msg, lang="Chinese")
            elif lang == "Chinese":
                logger.info(f"DEBUG: Chinese in EN channel - sending to both channels")
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")
                tr = await to_target(txt, "zh_to_en")
                logger.info(f"DEBUG: Translated '{txt}' to '{tr}' for English channel")
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
            else:
                logger.info(f"DEBUG: Meaningless in EN channel - sending to Chinese")
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")
        else:
            if lang == "Chinese":
                tr = await to_target(txt, "zh_to_en")
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
            elif lang == "English":
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], txt, msg, lang="English")
            else:
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], txt, msg, lang="English")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.webhook_id or not after.guild:
            return
        gid = after.guild.id
        neighbors = self._mirror_neighbors(gid, after.id)
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
        fake = after
        if after.channel.id == cfg["en_channel_id"]:
            await self.on_message(fake)
        elif after.channel.id == cfg["zh_channel_id"]:
            await self.on_message(fake)

    async def on_message_delete(self, msg: discord.Message):
        if msg.author.bot or msg.webhook_id or not msg.guild:
            return
        gid = msg.guild.id
        neighbors = self._mirror_neighbors(gid, msg.id)
        for ch_id, mid in list(neighbors.items()):
            try:
                ch = msg.guild.get_channel(ch_id) or await self.fetch_channel(ch_id)
                m = await ch.fetch_message(mid)
                await m.delete()
            except Exception:
                continue

def main():
    # 环境变量已经在文件开头处理，这里只需要验证
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

#test
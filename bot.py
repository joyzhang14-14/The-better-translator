#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional, Tuple, List, Dict
from io import BytesIO
from collections import deque, defaultdict

import aiohttp
import discord
from discord.ext import commands, tasks
from openai import AsyncOpenAI
import deepl
from dotenv import load_dotenv

from preprocess import preprocess, preprocess_with_emoji_extraction, extract_emojis, restore_emojis, FSURE_HEAD, FSURE_SEP, has_bao_de_pattern
import joy_cmds as prompt_mod
import health_server
from storage import storage
from translator import Translator
from gpt_handler import GPTHandler
from glossary_handler import glossary_handler

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE, "config.json")
DICTIONARY_PATH = os.path.join(BASE, "dictionary.json")
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
    body = lines[i:]
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
    if not s:
        return False
    t = s.strip()
    
    # Check if it's a Discord bot command (starts with !)
    if t.startswith("!"):
        return True
    
    # Check configured passthrough commands
    cmds = _merge_default(passthrough_cfg, gid).get("commands", [])
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
        # Initialize GPT handler and translator
        self.gpt_handler = GPTHandler(openai_client)
        self.translator = Translator(deepl_client, self.gpt_handler)
        # Message history for context-aware translation (2-minute window)
        # Structure: {(guild_id, channel_id, user_id): [(timestamp, content), ...]}
        self._user_message_history: Dict[Tuple[int, int, int], List[Tuple[float, str]]] = defaultdict(list)
        self.CONTEXT_WINDOW_SECONDS = 120  # 2 minutes

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
        global guild_dicts, passthrough_cfg
        
        # Load persistent data
        logger.info("Loading persistent data...")
        guild_dicts.update(await storage.load_json("dictionary", {}))
        
        # Load passthrough from local file only (not from cloud storage)
        passthrough_cfg.update(_load_json_or(PASSTHROUGH_PATH, {"default": {"commands": [], "fillers": []}}))
        
        # Load glossaries from cloud
        await glossary_handler.load_from_cloud()
        
        # Load problem reports from cloud and sync to local file
        await self._load_problem_reports()
        
        logger.info(f"Loaded {len(guild_dicts)} guilds in dictionary")
        
        self._mirror_load()
    
    async def _load_problem_reports(self):
        """Load problem reports from cloud and sync to local file"""
        try:
            # Use the same path as joy_cmds.py for consistency
            PROBLEM_PATH = os.path.abspath(os.path.join(BASE, "problem.json"))
            logger.info(f"Loading problem reports to path: {PROBLEM_PATH}")
            
            cloud_problems = await storage.load_json("problems", [])
            if cloud_problems:
                # Ensure directory exists
                os.makedirs(os.path.dirname(PROBLEM_PATH), exist_ok=True)
                
                # Save to local file
                with open(PROBLEM_PATH, "w", encoding="utf-8") as f:
                    json.dump(cloud_problems, f, ensure_ascii=False, indent=2)
                logger.info(f"Loaded {len(cloud_problems)} problem reports from cloud and saved to {PROBLEM_PATH}")
                
                # Verify the file was created
                if os.path.exists(PROBLEM_PATH):
                    file_size = os.path.getsize(PROBLEM_PATH)
                    logger.info(f"Problem report file created successfully: {PROBLEM_PATH} ({file_size} bytes)")
                else:
                    logger.error(f"Problem report file was not created: {PROBLEM_PATH}")
            else:
                logger.info("No problem reports found in cloud storage")
        except Exception as e:
            logger.error(f"Failed to load problem reports from cloud: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
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
        neighbors = self.mirror_map.get(gid, {}).get(src_id, {})
        return neighbors

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
        
        # Step 1: Convert traditional Chinese to simplified Chinese (consistent with system)
        t = self.gpt_handler.convert_traditional_to_simplified(t)
        
        # Step 2: Extract emojis before language detection to avoid emoji interference
        text_without_emojis, _ = extract_emojis(t)
        
        # Step 3: Process text without emojis for accurate language detection
        t2 = text_without_emojis
        t2 = re.sub(r"(e?m+)+", "em", t2, flags=re.IGNORECASE)
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
        en_count = len(re.findall(r"[A-Za-z]", t2))
        
        # Step 4: Language detection logic consistent with user requirements:
        # 1. Any Chinese character = Chinese (if no English)
        # 2. Mixed Chinese-English = Mixed (for dual translation)
        if zh_count > 0 and en_count > 0:
            logger.info(f"Mixed language detected ({zh_count} Chinese, {en_count} English), treating as Mixed")
            return "Mixed"
        elif zh_count > 0:
            logger.info(f"Pure Chinese detected ({zh_count} Chinese), treating as Chinese")
            return "Chinese"
        elif en_count > 0:
            logger.info(f"Pure English detected ({en_count} English), treating as English")
            return "English"
        else:
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
                model="gpt-5-mini",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
                max_completion_tokens=5
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

    async def _gpt5_determine_primary_language(self, text: str) -> str:
        """Use GPT5 to determine which language is primary for mixed language text"""
        sys = (
            "分析这段中英混合的文字，判断应该将其主要理解为中文还是英文。"
            "考虑语言的主体含义、语法结构、和交流意图。"
            "只回答 'Chinese' 或 'English'，不要其他解释。"
        )
        usr = f"分析文字: {text}"
        
        try:
            if not self.openai_client:
                logger.warning("No OpenAI client available, using character count fallback for Mixed language")
                t2 = re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", text)
                zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
                en_count = len(re.findall(r"[A-Za-z]", t2))
                return "Chinese" if zh_count >= en_count else "English"
                
            r = await self.openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
                max_completion_tokens=5
            )
            result = (r.choices[0].message.content or "").strip().lower()
            logger.info(f"GPT5 primary language determination: '{text}' -> '{result}'")
            
            if "chinese" in result:
                return "Chinese"
            elif "english" in result:
                return "English"
            else:
                # Fallback to character counting
                logger.warning(f"GPT5 returned unexpected result '{result}', using character count fallback")
                t2 = re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", text)
                zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
                en_count = len(re.findall(r"[A-Za-z]", t2))
                return "Chinese" if zh_count >= en_count else "English"
        except Exception as e:
            logger.error(f"GPT5 primary language determination failed: {e}")
            # Fallback to character counting
            t2 = re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", text)
            zh_count = len(re.findall(r"[\u4e00-\u9fff]", t2))
            en_count = len(re.findall(r"[A-Za-z]", t2))
            return "Chinese" if zh_count >= en_count else "English"

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
                model="gpt-5-mini",
                messages=[{"role":"system","content":sys},{"role":"user","content":usr}]
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



    def _add_message_to_history(self, guild_id: int, channel_id: int, user_id: int, content: str):
        """Add a message to user's history for context-aware translation"""
        if not content or not content.strip():
            return
        
        key = (guild_id, channel_id, user_id)
        current_time = time.time()
        
        # Add new message
        self._user_message_history[key].append((current_time, content.strip()))
        
        # Clean up old messages (older than 2 minutes)
        self._cleanup_message_history(key, current_time)
        
        # Keep only last 10 messages to prevent memory bloat
        if len(self._user_message_history[key]) > 10:
            self._user_message_history[key] = self._user_message_history[key][-10:]

    def _cleanup_message_history(self, key: Tuple[int, int, int], current_time: float):
        """Remove messages older than the context window"""
        cutoff_time = current_time - self.CONTEXT_WINDOW_SECONDS
        history = self._user_message_history[key]
        self._user_message_history[key] = [(ts, content) for ts, content in history if ts >= cutoff_time]

    def _get_context_messages(self, guild_id: int, channel_id: int, user_id: int) -> List[str]:
        """Get recent messages from user for context (excluding the current message)"""
        key = (guild_id, channel_id, user_id)
        current_time = time.time()
        
        # Clean up old messages first
        self._cleanup_message_history(key, current_time)
        
        # Get messages excluding the most recent one (which is the current message)
        history = self._user_message_history[key]
        if len(history) <= 1:
            return []
        
        # Return all but the last message (last message is the current one we just added)
        context_messages = [content for _, content in history[:-1]]
        return context_messages

    def _should_use_context_translation(self, guild_id: int, channel_id: int, user_id: int) -> bool:
        """Check if we should use context-aware translation based on recent message history"""
        context_messages = self._get_context_messages(guild_id, channel_id, user_id)
        # Use context translation if user has sent 1+ messages in the last 2 minutes
        return len(context_messages) >= 1



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
            show = await self.translator.translate_text(raw, "en_to_zh", cm, guild_id=gid_str)
        elif target_lang == "English" and ref_lang == "Chinese":
            show = await self.translator.translate_text(raw, "zh_to_en", cm, guild_id=gid_str)
        else:
            show = raw
        return jump, show, False

    async def _get_original_author(self, ref: discord.Message) -> discord.User:
        """Get the original author of a message, handling webhook messages by finding the original message via mirror mapping"""
        # If it's not a webhook message, return the original author
        if not ref.webhook_id:
            return ref.author
        
        # If it's a webhook message, try to find the original message through mirror mapping
        try:
            gid = ref.guild.id if ref.guild else 0
            neighbors = self._mirror_neighbors(gid, ref.id)
            
            # Look through all mirror mappings to find the original message
            for channel_id, message_id in neighbors.items():
                try:
                    original_msg = await self._fetch_message(ref.guild, channel_id, message_id)
                    if original_msg and not original_msg.webhook_id:
                        # Found the original non-webhook message
                        return original_msg.author
                except Exception:
                    continue
            
            # Alternative approach: search all mirror mappings in the guild to find where this webhook message is referenced
            guild_mirrors = self.mirror_map.get(gid, {})
            for src_msg_id, channel_mappings in guild_mirrors.items():
                for ch_id, mapped_msg_id in channel_mappings.items():
                    if mapped_msg_id == ref.id:
                        # Found the source message that created this webhook message
                        try:
                            # Try to fetch the original source message (should be non-webhook)
                            # We need to find which channel the source message is in
                            source_neighbors = self._mirror_neighbors(gid, src_msg_id)
                            for source_ch_id, _ in source_neighbors.items():
                                if source_ch_id != ref.channel.id:  # Different channel
                                    source_msg = await self._fetch_message(ref.guild, source_ch_id, src_msg_id)
                                    if source_msg and not source_msg.webhook_id:
                                        return source_msg.author
                            
                            # If no different channel found, try the source message directly
                            if src_msg_id != ref.id:  # Avoid infinite loop
                                source_msg = await self._fetch_message(ref.guild, ch_id, src_msg_id)
                                if source_msg and not source_msg.webhook_id:
                                    return source_msg.author
                        except Exception:
                            continue
                            
        except Exception:
            pass
        
        # Fallback to webhook author if we can't find the original
        return ref.author

    async def _make_top_reply_banner(self, ref: discord.Message, target_lang: str, target_channel_id: int, original_author: discord.User = None) -> str:
        reply_label = REPLY_LABEL_ZH if target_lang == "Chinese" else REPLY_LABEL_EN
        reply_icon = REPLY_ICON_DEFAULT
        jump, preview, only_image = await self._choose_jump_and_preview(ref, target_lang, target_channel_id)
        if only_image:
            preview = "[image]"
        preview = re.sub(r"\s+", " ", preview).strip()
        preview = _delink_for_reply(preview)
        preview = _shorten(preview, REPLY_PREVIEW_LIMIT)
        # Get the original author (prefer passed parameter, fallback to discovery)
        if original_author is None:
            original_author = await self._get_original_author(ref)
        return f"> {original_author.mention} {reply_icon} [{reply_label}]({jump}) {preview}".rstrip()

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


        top_banner = ""
        ref = await self._get_ref_message(msg)
        if ref is not None:
            try:
                # For replies, we need to find the original author of the referenced message
                # If ref is a webhook message, we need to find who originally sent it
                ref_original_author = None
                if ref.webhook_id:
                    # The referenced message is a webhook message, so we need to find its original author
                    ref_original_author = await self._get_original_author(ref)
                    # If we can't find the original author, try to create a user-like object from webhook info
                    if ref_original_author == ref.author and hasattr(ref, 'author') and hasattr(ref.author, 'display_name'):
                        logger.info(f"Could not find original author for webhook message {ref.id}, using display name: {ref.author.display_name}")
                        # Create a simple user-like object for mention purposes
                        # Note: This will still show as @unknown user but with the right display name
                        class WebhookUser:
                            def __init__(self, display_name):
                                self.display_name = display_name
                                self.mention = f"**{display_name}**"  # Bold display name instead of mention
                        
                        ref_original_author = WebhookUser(ref.author.display_name)
                else:
                    # The referenced message is a regular user message
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
                allowed_mentions=self.no_ping,
                wait=True,
            )
            try:
                if isinstance(sent, (discord.Message, discord.WebhookMessage)):
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
                tr = await self.translator.translate_text(text, direction, cm, guild_id=gid)
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
        patch_result = await self._process_star_patch_if_any_with_content(original_content, msg)
        if patch_result is not None:
            patched_content, original_msg_id = patch_result
        else:
            patched_content, original_msg_id = None, None
        
        # Update recent message ID only after patch check
        self._recent_user_message[msg.author.id] = msg.id
        
        cm = guild_dicts.get(gid, {})
        raw = msg.content or ""
        # Apply basic preprocessing (handles 6/666 -> 厉害 conversion)
        # Skip bao_de processing here - let translate_text handle it
        raw = preprocess(raw, "zh_to_en", skip_bao_de=True)
        
        if patched_content is not None:
            # Apply preprocessing and abbreviations to the patched result
            # Skip bao_de processing here - let translate_text handle it
            raw = preprocess(patched_content, "zh_to_en", skip_bao_de=True)
            
            # For star patches, edit existing messages instead of sending new ones
            await self._handle_star_patch_edit(raw, msg, cfg, gid, cm, original_msg_id)
            return
        
        # Check pass-through using processed text (after potential star patch)
        # Create a simple object with the required attributes
        class TempMessage:
            def __init__(self, content, attachments, guild):
                self.content = content
                self.attachments = attachments
                self.guild = guild
        
        
        temp_msg = TempMessage(raw, msg.attachments, msg.guild)
        if await self.is_pass_through(temp_msg):
            if is_en:
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], raw, msg, lang="Chinese")
            else:
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], raw, msg, lang="English")
            return
        txt = strip_banner(raw)
        # Convert traditional Chinese to simplified Chinese before language detection
        txt = self.gpt_handler.convert_traditional_to_simplified(txt)
        lang = await self.detect_language(txt)
        logger.info(f"LANGUAGE_DEBUG: Original message: '{msg.content}', Processed: '{txt}', Detected language: '{lang}'")
        
        # Add message to history AFTER processing (since context translation is disabled)
        self._add_message_to_history(msg.guild.id, msg.channel.id, msg.author.id, txt)
        
        # Get reply context for better translation accuracy (highest priority)
        reply_context = None
        ref = await self._get_ref_message(msg)
        if ref is not None:
            reply_context = strip_banner(ref.content or "")
        
        # Get message history context if no explicit reply (second priority)
        history_messages = None
        # TEMPORARILY DISABLE CONTEXT TRANSLATION to fix message duplication issues
        # if reply_context is None and self._should_use_context_translation(msg.guild.id, msg.channel.id, msg.author.id):
        #     history_messages = self._get_context_messages(msg.guild.id, msg.channel.id, msg.author.id)
        #     logger.info(f"DEBUG: Using message history context for user {msg.author.id}: {len(history_messages)} messages")
        
        async def to_target(text: str, direction: str) -> str:
            tr = await self.translator.translate_text(text, direction, cm, context=reply_context, history_messages=history_messages, guild_id=gid, user_name=msg.author.display_name)
            if tr == "/":
                return text
            return tr
        # SIMPLIFIED LOGIC: All messages from Chinese channel translate to English only
        # No matter what language they are, they all go to English channel
        if not is_en:  # From Chinese channel
            logger.info(f"Message from Chinese channel (lang={lang}): translating to English only")
            if lang == "Chinese" or lang == "Mixed":
                # Translate Chinese/Mixed to English
                tr = await to_target(txt, "zh_to_en")
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
            elif lang == "English":
                # English text, translate to Chinese but send as English? Or just forward?
                # Based on user request: translate to English (which might mean keep as English)
                tr = await to_target(txt, "en_to_zh")  # Translate for processing but...
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], txt, msg, lang="English")  # Send original English
            else:
                # Unknown language, send to English channel
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], txt, msg, lang="English")
        else:
            # From English channel - normal translation logic
            if lang == "English":
                # English message from English channel -> translate to Chinese channel only
                tr = await to_target(txt, "en_to_zh")
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], tr, msg, lang="Chinese")
            elif lang == "Chinese":
                # Chinese message from English channel -> send original to Chinese + translation to English
                logger.info(f"Chinese message from English channel: sending original to Chinese + translation to English")
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")
                tr = await to_target(txt, "zh_to_en")
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
            elif lang == "Mixed":
                logger.info(f"Processing mixed language from English channel: '{txt}'")
                logger.info(f"TIMELINE_DEBUG: About to send to Chinese channel - current message: '{msg.content}', processed: '{txt}'")
                # For Mixed from English channel, send original to Chinese + determine translation direction
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")
                
                # For Mixed language from English channel, always translate to English
                # GPT5 determines which translation approach to use
                primary_lang = await self._gpt5_determine_primary_language(txt)
                logger.info(f"GPT5_DEBUG: Determined primary language for '{txt}' as '{primary_lang}'")
                
                if primary_lang == "Chinese":
                    # Treat as Chinese -> translate to English
                    tr = await to_target(txt, "zh_to_en")
                elif primary_lang == "English":
                    # Treat as English -> translate to clean English (remove Chinese parts)
                    tr = await to_target(txt, "en_to_zh")  # First pass through translation
                    # Then translate back to get clean English
                    tr = await to_target(tr, "zh_to_en") if tr != "/" else txt
                else:
                    # Fallback: treat as Chinese -> translate to English
                    tr = await to_target(txt, "zh_to_en")
                
                # Always send English result to English channel
                await self.send_via_webhook(cfg["en_webhook_url"], cfg["en_channel_id"], tr, msg, lang="English")
                logger.info(f"Mixed->English translation sent to English channel: '{tr}'")
            else:
                await self.send_via_webhook(cfg["zh_webhook_url"], cfg["zh_channel_id"], txt, msg, lang="Chinese")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.webhook_id or not after.guild:
            return
        gid = after.guild.id
        neighbors = self._mirror_neighbors(gid, after.id)
        if not neighbors:
            # No existing mirrors, this shouldn't be processed as edit
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
        # After deleting old mirrors, regenerate translations for the edited message
        cfg = self._guild_cfg(str(gid))
        if not cfg:
            return
        # Process the edited message as a new message to create updated translations
        if after.channel.id in [cfg["en_channel_id"], cfg["zh_channel_id"]]:
            await self.on_message(after)

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
    2
    logger.info("Starting Discord Translator Bot...")
    logger.info(f"Bot will run on {len(config.get('guilds', {}))} configured guilds")
    bot = TranslatorBot()
    prompt_mod.register_commands(
        bot=bot,
        config=config,
        guild_dicts=guild_dicts,
        dictionary_path=DICTIONARY_PATH,
        guild_abbrs={},
        abbr_path="",
        can_use=lambda g, m: bot.is_admin_user(g, m),
    )
    print("bot running")
    bot.run(config["discord_token"])

if __name__ == "__main__":
    main()

#test
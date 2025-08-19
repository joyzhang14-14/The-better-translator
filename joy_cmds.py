import os
import json
import logging
import asyncio
import time
import uuid
from typing import Dict, List, Optional, Any
from discord.ext import commands
import discord
from storage import storage

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
PASSTHROUGH_PATH = os.path.join(os.path.dirname(__file__), "passthrough.json")
GLOSSARIES_PATH = os.path.join(os.path.dirname(__file__), "glossaries.json")
PROBLEM_PATH = os.path.join(os.path.dirname(__file__), "problem.json")

# Global storage for pending interactions
pending_glossary_sessions: Dict[str, Dict[str, Any]] = {}

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_json_or(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            return json.loads(txt) if txt else fallback
    except Exception:
        return fallback

def _ensure_admin_block(config, gid: str):
    g = config.setdefault("guilds", {}).setdefault(gid, {})
    a = g.setdefault("admin", {})
    a.setdefault("allowed_user_ids", [])
    a.setdefault("allowed_role_ids", [])
    a.setdefault("require_manage_guild", True)
    return a

def _is_whitelist_user(config, guild_id: int, user_id: int) -> bool:
    gid = str(guild_id)
    a = _ensure_admin_block(config, gid)
    return user_id in set(a.get("allowed_user_ids", []))

def _ensure_pt_commands(cmds):
    try:
        if not os.path.exists(PASSTHROUGH_PATH):
            data = {"default": {"commands": []}}
        else:
            with open(PASSTHROUGH_PATH, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                data = json.loads(txt) if txt else {"default": {"commands": []}}
        base = data.setdefault("default", {}).setdefault("commands", [])
        exist = set(c.lower() for c in base)
        for c in cmds:
            if c.lower() not in exist:
                base.append(c)
        _save_json(PASSTHROUGH_PATH, data)
    except Exception:
        pass

class ErrorSelectionView(discord.ui.View):
    def __init__(self, *, timeout=36000):  # 10 hours timeout
        super().__init__(timeout=timeout)
    
    @discord.ui.button(label="1. 报告翻译逻辑错误\nreport bot logical bug", style=discord.ButtonStyle.red, emoji="🐛")
    async def report_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Create and send the problem report modal
        modal = ProblemReportModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="2. 添加术语\nadd prompt", style=discord.ButtonStyle.green, emoji="📝")
    async def add_glossary(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Start the glossary addition process
        session_id = str(uuid.uuid4())
        guild_id = str(interaction.guild.id)
        user_id = interaction.user.id
        
        # Initialize session data
        pending_glossary_sessions[session_id] = {
            "guild_id": guild_id,
            "user_id": user_id,
            "timestamp": time.time(),
            "step": "mandatory_selection",
            "data": {}
        }
        
        # Show mandatory/optional selection
        view = MandatorySelectionView(session_id)
        await interaction.response.send_message(
            "添加术语为强制替换还是选择性替换\nIs adding a prompt a mandatory or optional replacement?",
            view=view,
            ephemeral=True
        )
    
    async def on_timeout(self):
        # Disable all buttons when timed out
        for item in self.children:
            item.disabled = True

class ProblemReportModal(discord.ui.Modal, title="问题报告 Problem Report"):
    def __init__(self):
        super().__init__()
    
    problem_description = discord.ui.TextInput(
        label="告诉开发者关于你遇到的问题\nProvide dev more details about the issue you encountered",
        style=discord.TextStyle.paragraph,
        placeholder="请详细描述遇到的翻译问题...\nPlease describe the translation issue in detail...",
        max_length=1000,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        # Save problem report to problem.json
        try:
            problems = _load_json_or(PROBLEM_PATH, [])
            problem_entry = {
                "timestamp": time.time(),
                "guild_id": str(interaction.guild.id),
                "user_id": interaction.user.id,
                "username": interaction.user.display_name,
                "description": self.problem_description.value
            }
            problems.append(problem_entry)
            _save_json(PROBLEM_PATH, problems)
            
            await interaction.response.send_message("✅已成功提交 submitted", ephemeral=True)
            logger.info(f"Problem report saved: {problem_entry}")
        except Exception as e:
            logger.error(f"Failed to save problem report: {e}")
            await interaction.response.send_message("❌保存失败 save failed", ephemeral=True)

class MandatorySelectionView(discord.ui.View):
    def __init__(self, session_id: str, *, timeout=600):  # 10 minutes timeout
        super().__init__(timeout=timeout)
        self.session_id = session_id
    
    @discord.ui.button(label="1. 强制性 mandatory", style=discord.ButtonStyle.red)
    async def mandatory_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, False)  # false = mandatory
    
    @discord.ui.button(label="2. 选择性 optional", style=discord.ButtonStyle.green)
    async def optional_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, True)  # true = optional (needs GPT)
    
    async def _handle_selection(self, interaction: discord.Interaction, needs_gpt: bool):
        if self.session_id not in pending_glossary_sessions:
            await interaction.response.send_message("❌会话已过期 Session expired", ephemeral=True)
            return
        
        session = pending_glossary_sessions[self.session_id]
        session["data"]["needs_gpt"] = needs_gpt
        session["step"] = "source_language_selection"
        session["timestamp"] = time.time()
        
        # Show source language selection
        view = SourceLanguageSelectionView(self.session_id)
        await interaction.response.edit_message(
            content="需识别文字的语言\nThe language of the text to be recognized",
            view=view
        )
    
    async def on_timeout(self):
        if self.session_id in pending_glossary_sessions:
            del pending_glossary_sessions[self.session_id]

class SourceLanguageSelectionView(discord.ui.View):
    def __init__(self, session_id: str, *, timeout=600):
        super().__init__(timeout=timeout)
        self.session_id = session_id
    
    @discord.ui.button(label="1. 中文 Chinese", style=discord.ButtonStyle.primary)
    async def chinese_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "中文")
    
    @discord.ui.button(label="2. 英文 English", style=discord.ButtonStyle.primary)
    async def english_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "英文")
    
    async def _handle_selection(self, interaction: discord.Interaction, language: str):
        if self.session_id not in pending_glossary_sessions:
            await interaction.response.send_message("❌会话已过期 Session expired", ephemeral=True)
            return
        
        session = pending_glossary_sessions[self.session_id]
        session["data"]["source_language"] = language
        session["step"] = "source_text_input"
        session["timestamp"] = time.time()
        
        # Show source text input modal
        modal = SourceTextModal(self.session_id)
        await interaction.response.send_modal(modal)
    
    async def on_timeout(self):
        if self.session_id in pending_glossary_sessions:
            del pending_glossary_sessions[self.session_id]

class SourceTextModal(discord.ui.Modal, title="输入识别文字 Input Recognition Text"):
    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
    
    source_text = discord.ui.TextInput(
        label="请输入需要识别的文字\nPlease enter the text that needs to be recognized",
        style=discord.TextStyle.short,
        placeholder="例如: ik / 示例",
        max_length=100,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.session_id not in pending_glossary_sessions:
            await interaction.response.send_message("❌会话已过期 Session expired", ephemeral=True)
            return
        
        session = pending_glossary_sessions[self.session_id]
        session["data"]["source_text"] = self.source_text.value.strip()
        session["step"] = "target_language_selection"
        session["timestamp"] = time.time()
        
        # Show target language selection
        view = TargetLanguageSelectionView(self.session_id)
        await interaction.response.send_message(
            "需替换文字的语言\nThe language of the text to be replaced",
            view=view,
            ephemeral=True
        )

class TargetLanguageSelectionView(discord.ui.View):
    def __init__(self, session_id: str, *, timeout=600):
        super().__init__(timeout=timeout)
        self.session_id = session_id
    
    @discord.ui.button(label="1. 中文 Chinese", style=discord.ButtonStyle.primary)
    async def chinese_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "中文")
    
    @discord.ui.button(label="2. 英文 English", style=discord.ButtonStyle.primary)
    async def english_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_selection(interaction, "英文")
    
    async def _handle_selection(self, interaction: discord.Interaction, language: str):
        if self.session_id not in pending_glossary_sessions:
            await interaction.response.send_message("❌会话已过期 Session expired", ephemeral=True)
            return
        
        session = pending_glossary_sessions[self.session_id]
        session["data"]["target_language"] = language
        session["step"] = "target_text_input"
        session["timestamp"] = time.time()
        
        # Show target text input modal
        modal = TargetTextModal(self.session_id)
        await interaction.response.send_modal(modal)
    
    async def on_timeout(self):
        if self.session_id in pending_glossary_sessions:
            del pending_glossary_sessions[self.session_id]

class TargetTextModal(discord.ui.Modal, title="输入替换文字 Input Replacement Text"):
    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
    
    target_text = discord.ui.TextInput(
        label="请输入需要替换的文字\nPlease enter the text that needs to be replaced",
        style=discord.TextStyle.short,
        placeholder="例如: I know / 我知道",
        max_length=200,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.session_id not in pending_glossary_sessions:
            await interaction.response.send_message("❌会话已过期 Session expired", ephemeral=True)
            return
        
        session = pending_glossary_sessions[self.session_id]
        session["data"]["target_text"] = self.target_text.value.strip()
        
        # Save to glossaries.json
        try:
            await self._save_glossary_entry(session)
            await interaction.response.send_message("✅术语添加成功 Glossary entry added successfully", ephemeral=True)
            logger.info(f"Glossary entry added: {session['data']}")
        except Exception as e:
            logger.error(f"Failed to save glossary entry: {e}")
            await interaction.response.send_message("❌保存失败 Save failed", ephemeral=True)
        finally:
            # Clean up session
            if self.session_id in pending_glossary_sessions:
                del pending_glossary_sessions[self.session_id]
    
    async def _save_glossary_entry(self, session):
        glossaries = _load_json_or(GLOSSARIES_PATH, {})
        guild_id = session["guild_id"]
        
        if guild_id not in glossaries:
            glossaries[guild_id] = {}
        
        # Generate unique entry ID
        entry_id = str(uuid.uuid4())
        
        # Create glossary entry
        entry = {
            "needs_gpt": session["data"]["needs_gpt"],
            "source_language": session["data"]["source_language"],
            "source_text": session["data"]["source_text"],
            "target_language": session["data"]["target_language"],
            "target_text": session["data"]["target_text"]
        }
        
        glossaries[guild_id][entry_id] = entry
        
        # Save to local file
        _save_json(GLOSSARIES_PATH, glossaries)
        
        # Save to cloud storage
        await storage.save_json("glossaries", glossaries)

def register_commands(bot: commands.Bot, config, guild_dicts, dictionary_path, guild_abbrs, abbr_path, can_use):
    mgmt_cmds = ["!setrequire", "!allowuser", "!denyuser", "!allowrole", "!denyrole", "!error"]
    _ensure_pt_commands(mgmt_cmds)

    @bot.command(name="error")
    async def error_command(ctx):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        
        # Create and send the error selection view
        view = ErrorSelectionView()
        await ctx.reply(
            "请选择操作类型 Please select operation type:",
            view=view,
            mention_author=False
        )

    @bot.command(name="setrequire")
    async def setrequire(ctx, mode: str):
        gid = str(ctx.guild.id)
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        m = mode.strip().lower()
        if m not in ("on", "off", "true", "false", "1", "0"):
            return await ctx.reply("用法: !setrequire on|off", mention_author=False)
        val = m in ("on", "true", "1")
        a = _ensure_admin_block(config, gid)
        a["require_manage_guild"] = val
        _save_json(CONFIG_PATH, config)
        await ctx.reply(("已开启限制 Restriction enabled" if val else "已关闭限制 Restriction disabled") + " (setrequire)", mention_author=False)

    @bot.command(name="allowuser")
    async def allowuser(ctx):
        gid = str(ctx.guild.id)
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        mentions = ctx.message.mentions
        if not mentions:
            return await ctx.reply("用法: !allowuser @User [@User...]", mention_author=False)
        a = _ensure_admin_block(config, gid)
        cur = set(a.get("allowed_user_ids", []))
        for u in mentions:
            cur.add(u.id)
        a["allowed_user_ids"] = list(cur)
        _save_json(CONFIG_PATH, config)
        names = ", ".join(m.display_name for m in mentions)
        await ctx.reply(f"✅已加入 added: {names}", mention_author=False)

    @bot.command(name="denyuser")
    async def denyuser(ctx):
        gid = str(ctx.guild.id)
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        mentions = ctx.message.mentions
        if not mentions:
            return await ctx.reply("用法: !denyuser @User [@User...]", mention_author=False)
        a = _ensure_admin_block(config, gid)
        cur = set(a.get("allowed_user_ids", []))
        for u in mentions:
            if u.id in cur:
                cur.remove(u.id)
        a["allowed_user_ids"] = list(cur)
        _save_json(CONFIG_PATH, config)
        names = ", ".join(m.display_name for m in mentions)
        await ctx.reply(f"✅已移出 removed: {names}", mention_author=False)

    @bot.command(name="allowrole")
    async def allowrole(ctx):
        gid = str(ctx.guild.id)
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        roles = ctx.message.role_mentions
        if not roles:
            return await ctx.reply("用法: !allowrole @Role [@Role...]", mention_author=False)
        a = _ensure_admin_block(config, gid)
        cur = set(a.get("allowed_role_ids", []))
        for r in roles:
            cur.add(r.id)
        a["allowed_role_ids"] = list(cur)
        _save_json(CONFIG_PATH, config)
        names = ", ".join(r.name for r in roles)
        await ctx.reply(f"✅已加入 added: {names}", mention_author=False)

    @bot.command(name="denyrole")
    async def denyrole(ctx):
        gid = str(ctx.guild.id)
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        roles = ctx.message.role_mentions
        if not roles:
            return await ctx.reply("用法: !denyrole @Role [@Role...]", mention_author=False)
        a = _ensure_admin_block(config, gid)
        cur = set(a.get("allowed_role_ids", []))
        for r in roles:
            if r.id in cur:
                cur.remove(r.id)
        a["allowed_role_ids"] = list(cur)
        _save_json(CONFIG_PATH, config)
        names = ", ".join(r.name for r in roles)
        await ctx.reply(f"✅已移出 removed: {names}", mention_author=False)

    @bot.command(name="test")
    async def test(ctx):
        logger.info("TEST command called")
        await ctx.reply("Bot is working! Test successful.", mention_author=False)

    # Clean up expired sessions periodically
    @bot.event
    async def on_ready():
        if not hasattr(bot, '_cleanup_task_started'):
            bot._cleanup_task_started = True
            asyncio.create_task(_cleanup_expired_sessions())

async def _cleanup_expired_sessions():
    """Clean up expired glossary sessions every minute"""
    while True:
        try:
            current_time = time.time()
            expired_sessions = []
            
            for session_id, session in pending_glossary_sessions.items():
                # 10 minutes timeout
                if current_time - session["timestamp"] > 600:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del pending_glossary_sessions[session_id]
                logger.info(f"Cleaned up expired session: {session_id}")
            
            await asyncio.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Error in session cleanup: {e}")
            await asyncio.sleep(60)
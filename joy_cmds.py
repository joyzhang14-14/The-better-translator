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
# Use absolute path for problem.json to ensure it's in the current working directory
PROBLEM_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "problem.json"))

# Global storage for pending interactions
pending_glossary_sessions: Dict[str, Dict[str, Any]] = {}

# Global storage for tracking user's popup messages that should be cleaned up
# Structure: {user_id: {"last_popup": message_object, "main_message": message_object}}
user_popup_messages: Dict[int, Dict[str, discord.Message]] = {}

def _save_json(path, data):
    try:
        # Ensure the directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        # Create a temporary file first, then rename to ensure atomic write
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # Atomic rename
        os.replace(temp_path, path)
        logger.info(f"Successfully saved JSON to {path}")
        
    except Exception as e:
        logger.error(f"Failed to save JSON to {path}: {e}")
        # Clean up temp file if it exists
        temp_path = path + ".tmp"
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise

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

async def _cleanup_old_popups(user_id: int):
    """Clean up the most recent popup message for immediate deletion"""
    if user_id not in user_popup_messages:
        return
    
    user_messages = user_popup_messages[user_id]
    
    # Delete the last popup message if it exists
    if "last_popup" in user_messages:
        try:
            last_popup = user_messages["last_popup"]
            await last_popup.delete()
            logger.info(f"Deleted last popup message for user {user_id}: {last_popup.content[:50] if last_popup.content else 'No content'}...")
            del user_messages["last_popup"]
        except Exception as e:
            logger.warning(f"Failed to delete last popup message: {e}")
            # Remove the reference even if deletion failed
            if "last_popup" in user_messages:
                del user_messages["last_popup"]

def _track_popup_message(user_id: int, message: discord.Message):
    """Track a popup message for later cleanup"""
    if user_id not in user_popup_messages:
        user_popup_messages[user_id] = {}
    
    # Check if this is the main selection message
    if message.content and "请选择操作类型 Please select operation type:" in message.content:
        user_popup_messages[user_id]["main_message"] = message
        logger.info(f"Tracking main selection message for user {user_id}")
    else:
        user_popup_messages[user_id]["last_popup"] = message
        logger.info(f"Tracking popup message for user {user_id}: {message.content[:50] if message.content else 'No content'}...")

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
    
    @discord.ui.button(label="1. 报告翻译逻辑错误 report bot logical bug", style=discord.ButtonStyle.red)
    async def report_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing modal
        await _cleanup_old_popups(interaction.user.id)
        
        # Create and send the problem report modal, don't pass main message for deletion
        modal = ProblemReportModal(None)  # Don't delete main message
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="2. 添加术语 add prompt", style=discord.ButtonStyle.green)
    async def add_glossary(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_old_popups(interaction.user.id)
        
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
            # Don't store original_message to avoid deleting main selection message
        }
        
        # Show mandatory/optional selection
        view = MandatorySelectionView(session_id)
        await interaction.response.send_message(
            "添加术语为强制替换还是选择性替换\nIs adding a prompt a mandatory or optional replacement?",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="3. 查看术语 list prompts", style=discord.ButtonStyle.secondary)
    async def list_glossaries(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_old_popups(interaction.user.id)
        
        guild_id = str(interaction.guild.id)
        glossaries = _load_json_or(GLOSSARIES_PATH, {})
        guild_glossaries = glossaries.get(guild_id, {})
        
        if not guild_glossaries:
            await interaction.response.send_message("📋 本群组暂无术语 No glossaries in this guild", ephemeral=True)
        else:
            # Format glossaries list
            lines = ["📋 **术语列表 Glossary List**\n"]
            count = 0
            for entry_id, entry in guild_glossaries.items():
                count += 1
                emoji_type = ":red_circle:" if not entry["needs_gpt"] else ":yellow_circle:"
                replacement_type = "强制性Mandatory" if not entry["needs_gpt"] else "选择性Optional"
                
                # Convert language names to bilingual format
                source_lang_display = "中文Chinese" if entry['source_language'] == "中文" else "英文English"
                target_lang_display = "中文Chinese" if entry['target_language'] == "中文" else "英文English"
                
                line = (f"`{count}.` {emoji_type} {replacement_type} | "
                       f"{source_lang_display}: `{entry['source_text']}` → "
                       f"{target_lang_display}: `{entry['target_text']}`")
                lines.append(line)
                
                # Limit to 15 entries to avoid message length issues
                if count >= 15:
                    lines.append(f"\n... 还有 {len(guild_glossaries) - 15} 个术语 (and {len(guild_glossaries) - 15} more)")
                    break
            
            result = "\n".join(lines)
            if len(result) > 1900:  # Discord message limit
                result = result[:1900] + "...\n(消息过长已截断 Message truncated)"
            
            await interaction.response.send_message(result, ephemeral=True)
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
            
    
    @discord.ui.button(label="4. 删除术语 delete prompt", style=discord.ButtonStyle.danger)
    async def delete_glossary(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_old_popups(interaction.user.id)
        
        guild_id = str(interaction.guild.id)
        glossaries = _load_json_or(GLOSSARIES_PATH, {})
        guild_glossaries = glossaries.get(guild_id, {})
        
        if not guild_glossaries:
            await interaction.response.send_message("❌ 本群组暂无术语可删除 No glossaries to delete in this guild", ephemeral=True)
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
            return
        
        # Create selection dropdown
        view = DeleteGlossaryView(guild_id, guild_glossaries)
        await interaction.response.send_message(
            "🗑️ 选择要删除的术语 Select glossary to delete:",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="5. 术语检测设置 prompt detection settings", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_glossary_detection(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_old_popups(interaction.user.id)
        
        guild_id = str(interaction.guild.id)
        config = _load_json_or(CONFIG_PATH, {})
        
        # Get current glossary detection status (default: enabled)
        guild_config = config.get("guilds", {}).get(guild_id, {})
        current_status = guild_config.get("glossary_enabled", True)
        
        logger.info(f"PROMPT_DEBUG: Guild {guild_id} glossary status: {current_status}")
        
        # Create toggle view (no longer need to pass current_status as it reads from config)
        view = GlossaryToggleView(guild_id)
        status_text = "启用 Enabled" if current_status else "禁用 Disabled"
        await interaction.response.send_message(
            f"**术语检测设置 Prompt Detection Settings**\n\n"
            f"**当前状态 Current Status**: {status_text}\n"
            f"**说明 Description**:\n"
            f"启用 Enabled: 翻译可能较慢但更准确 Translation may be slower but more accurate\n"
            f"禁用 Disabled: 翻译更快但可能不够准确 Translation faster but may be less accurate",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    async def on_timeout(self):
        # Disable all buttons when timed out
        for item in self.children:
            item.disabled = True

class GlossaryToggleView(discord.ui.View):
    def __init__(self, guild_id: str, *, timeout=300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        # Don't store current_status, always read from config to get latest state
    
    def _get_current_status(self) -> bool:
        """Get real-time glossary status from config file"""
        config = _load_json_or(CONFIG_PATH, {})
        guild_config = config.get("guilds", {}).get(self.guild_id, {})
        status = guild_config.get("glossary_enabled", True)
        logger.info(f"PROMPT_DEBUG: Reading real-time status for guild {self.guild_id}: {status}")
        return status
    
    @discord.ui.button(label="启用术语检测 Enable Prompt Detection", style=discord.ButtonStyle.green)
    async def enable_glossary(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popup first
        await _cleanup_old_popups(interaction.user.id)
        
        # Get real-time status
        current_status = self._get_current_status()
        if current_status:
            await interaction.response.send_message("术语检测已经启用 Prompt detection is already enabled", ephemeral=True)
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
            return
        
        # Enable glossary detection
        config = _load_json_or(CONFIG_PATH, {})
        config.setdefault("guilds", {}).setdefault(self.guild_id, {})["glossary_enabled"] = True
        _save_json(CONFIG_PATH, config)
        
        await interaction.response.send_message(
            "**术语检测已启用 Prompt Detection Enabled**\n\n"
            "翻译可能会变得稍慢，但会更加准确\n"
            "Translation may become slightly slower, but will be more accurate\n\n"
            "设置已保存 Settings saved",
            ephemeral=True
        )
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="禁用术语检测 Disable Prompt Detection", style=discord.ButtonStyle.red)
    async def disable_glossary(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popup first
        await _cleanup_old_popups(interaction.user.id)
        
        # Get real-time status
        current_status = self._get_current_status()
        if not current_status:
            await interaction.response.send_message("术语检测已经禁用 Prompt detection is already disabled", ephemeral=True)
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
            return
        
        # Disable glossary detection
        config = _load_json_or(CONFIG_PATH, {})
        config.setdefault("guilds", {}).setdefault(self.guild_id, {})["glossary_enabled"] = False
        _save_json(CONFIG_PATH, config)
        
        await interaction.response.send_message(
            "**术语检测已禁用 Prompt Detection Disabled**\n\n"
            "翻译结果会出得更快，不过翻译结果可能会不准确\n"
            "Translation results will be faster, but may be less accurate\n\n"
            "设置已保存 Settings saved",
            ephemeral=True
        )
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class DeleteGlossaryView(discord.ui.View):
    def __init__(self, guild_id: str, guild_glossaries: dict, *, timeout=600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.guild_glossaries = guild_glossaries
        
        # Create dropdown with glossary options
        options = []
        count = 0
        for entry_id, entry in guild_glossaries.items():
            count += 1
            replacement_type = "🔴" if not entry["needs_gpt"] else "🟡"
            label = f"{replacement_type} {entry['source_text']} → {entry['target_text']}"
            # Truncate label if too long
            if len(label) > 90:
                label = label[:87] + "..."
            
            description = f"{entry['source_language']} → {entry['target_language']}"
            options.append(discord.SelectOption(
                label=label,
                value=entry_id,
                description=description,
            ))
            
            # Discord dropdown limit is 25 options
            if count >= 25:
                break
        
        if options:
            select = DeleteGlossarySelect(self.guild_id, options)
            self.add_item(select)
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class DeleteGlossarySelect(discord.ui.Select):
    def __init__(self, guild_id: str, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="选择要删除的术语... Select glossary to delete...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        selected_entry_id = self.values[0]
        
        # Load current glossaries
        glossaries = _load_json_or(GLOSSARIES_PATH, {})
        guild_glossaries = glossaries.get(self.guild_id, {})
        
        if selected_entry_id not in guild_glossaries:
            await interaction.response.send_message("❌ 术语不存在 Glossary not found", ephemeral=True)
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
            return
        
        # Get the entry details for confirmation
        entry = guild_glossaries[selected_entry_id]
        emoji_type = ":red_circle:" if not entry["needs_gpt"] else ":yellow_circle:"
        replacement_type = "强制性Mandatory" if not entry["needs_gpt"] else "选择性Optional"
        
        # Convert language names to bilingual format
        source_lang_display = "中文Chinese" if entry['source_language'] == "中文" else "英文English"
        target_lang_display = "中文Chinese" if entry['target_language'] == "中文" else "英文English"
        
        # Show confirmation
        view = DeleteConfirmationView(self.guild_id, selected_entry_id, entry)
        await interaction.response.send_message(
            f"🗑️ **确认删除术语 Confirm Delete Glossary**\n\n"
            f"**类型 Type**: {emoji_type} {replacement_type}\n"
            f"**源文字 Source**: {source_lang_display}: `{entry['source_text']}`\n"
            f"**目标文字 Target**: {target_lang_display}: `{entry['target_text']}`\n\n"
            f"❗ 此操作不可撤销 This action cannot be undone",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")

class DeleteConfirmationView(discord.ui.View):
    def __init__(self, guild_id: str, entry_id: str, entry: dict, *, timeout=300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.entry_id = entry_id
        self.entry = entry
    
    @discord.ui.button(label="确认删除 Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Load current glossaries
            glossaries = _load_json_or(GLOSSARIES_PATH, {})
            
            # Remove the entry
            if self.guild_id in glossaries and self.entry_id in glossaries[self.guild_id]:
                del glossaries[self.guild_id][self.entry_id]
                
                # Clean up empty guild entry
                if not glossaries[self.guild_id]:
                    del glossaries[self.guild_id]
                
                # Save to local file
                _save_json(GLOSSARIES_PATH, glossaries)
                
                # Save to cloud storage
                await storage.save_json("glossaries", glossaries)
                
                # Update glossary handler directly and save to local file
                from glossary_handler import glossary_handler
                glossary_handler.glossaries = glossaries
                glossary_handler._save_local_glossaries()
                
                await interaction.response.send_message(
                    f"✅ 术语删除成功 Glossary deleted successfully\n"
                    f"`{self.entry['source_text']}` → `{self.entry['target_text']}`",
                    ephemeral=True
                )
                logger.info(f"Glossary entry deleted: {self.entry}")
            else:
                await interaction.response.send_message("❌ 术语不存在 Glossary not found", ephemeral=True)
                
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
        except Exception as e:
            logger.error(f"Failed to delete glossary entry: {e}")
            await interaction.response.send_message("❌ 删除失败 Delete failed", ephemeral=True)
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
    
    @discord.ui.button(label="取消 Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ 已取消删除 Delete cancelled", ephemeral=True)
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class ProblemReportModal(discord.ui.Modal, title="问题报告 Problem Report"):
    def __init__(self, original_message=None):
        super().__init__()
        self.original_message = original_message
    
    problem_description = discord.ui.TextInput(
        label="描述遇到的问题 Describe the issue",
        style=discord.TextStyle.paragraph,
        placeholder="请详细描述遇到的翻译问题...\nPlease describe the translation issue in detail...",
        max_length=1000,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        # Save problem report to problem.json
        try:
            logger.info(f"Starting to save problem report from user {interaction.user.display_name}")
            logger.info(f"PROBLEM_PATH: {PROBLEM_PATH}")
            
            # Load existing problems
            problems = _load_json_or(PROBLEM_PATH, [])
            logger.info(f"Loaded {len(problems)} existing problems")
            
            # Create new problem entry
            problem_entry = {
                "timestamp": time.time(),
                "guild_id": str(interaction.guild.id),
                "user_id": interaction.user.id,
                "username": interaction.user.display_name,
                "description": self.problem_description.value
            }
            problems.append(problem_entry)
            logger.info(f"Created problem entry: {problem_entry}")
            
            # Save to local file with enhanced error handling
            logger.info(f"Attempting to save {len(problems)} problems to {PROBLEM_PATH}")
            _save_json(PROBLEM_PATH, problems)
            
            # Verify the save by reading back
            saved_problems = _load_json_or(PROBLEM_PATH, [])
            logger.info(f"Verification: file now contains {len(saved_problems)} problems")
            
            # Also save to cloud storage
            try:
                await storage.save_json("problems", problems)
                logger.info(f"Problem report saved to cloud: {problem_entry}")
            except Exception as cloud_error:
                logger.error(f"Failed to save problem report to cloud: {cloud_error}")
            
            await interaction.response.send_message("✅已成功提交 submitted", ephemeral=True)
            logger.info(f"Problem report successfully processed: {problem_entry}")
            
            # Delete the original bot message to clean up interface
            if self.original_message:
                try:
                    await self.original_message.delete()
                    logger.info("Deleted original bot message after problem report submission")
                except Exception as delete_error:
                    logger.warning(f"Failed to delete original message: {delete_error}")
            
        except Exception as e:
            logger.error(f"Failed to save problem report: {e}")
            logger.error(f"Error type: {type(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
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
        await interaction.response.send_message(
            "需识别文字的语言\nThe language of the text to be recognized",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
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
        label="输入识别文字 Enter source text",
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
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")

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
        label="输入替换文字 Enter target text",
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
            
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
                    
        except Exception as e:
            logger.error(f"Failed to save glossary entry: {e}")
            await interaction.response.send_message("❌保存失败 Save failed", ephemeral=True)
            # Track this popup message for cleanup
            _track_popup_message(interaction.user.id, await interaction.original_response())
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
        
        # Reload glossary handler to pick up new data
        from glossary_handler import glossary_handler
        glossary_handler.glossaries[guild_id] = glossaries[guild_id]
        glossary_handler._save_local_glossaries()

def register_commands(bot: commands.Bot, config, guild_dicts, dictionary_path, guild_abbrs, abbr_path, can_use):
    mgmt_cmds = ["!setrequire", "!allowuser", "!denyuser", "!allowrole", "!denyrole", "!bot14"]
    _ensure_pt_commands(mgmt_cmds)

    @bot.command(name="bot14")
    async def bot14_command(ctx):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        
        # Clean up old popups before showing main selection
        await _cleanup_old_popups(ctx.author.id)
        
        # Create and send the error selection view
        # VERSION: v2.1.5 - Update version for major feature additions (Minor +1) or bug fixes (Patch +1)
        # Format: Major.Minor.Patch (e.g., v2.1.0 for new features, v2.0.1 for bug fixes)
        view = ErrorSelectionView()
        message = await ctx.reply(
            "v2.1.5 请选择操作类型 Please select operation type:",
            view=view,
            mention_author=False
        )
        
        # Track this main selection message (it will be preserved during cleanup)
        _track_popup_message(ctx.author.id, message)

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
    
    @bot.command(name="debug_paths")
    async def debug_paths(ctx):
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        
        import os
        BASE = os.path.dirname(__file__)
        bot_problem_path = os.path.abspath(os.path.join(BASE, "problem.json"))
        joy_cmds_problem_path = PROBLEM_PATH
        
        # Check if files exist
        bot_exists = os.path.exists(bot_problem_path)
        joy_exists = os.path.exists(joy_cmds_problem_path)
        
        # Get file contents if they exist
        bot_content = "File not found"
        joy_content = "File not found"
        
        if bot_exists:
            try:
                with open(bot_problem_path, 'r', encoding='utf-8') as f:
                    bot_data = json.load(f)
                    bot_content = f"{len(bot_data)} problems"
            except:
                bot_content = "Error reading file"
        
        if joy_exists:
            try:
                with open(joy_cmds_problem_path, 'r', encoding='utf-8') as f:
                    joy_data = json.load(f)
                    joy_content = f"{len(joy_data)} problems"
            except:
                joy_content = "Error reading file"
        
        debug_info = (
            f"**Path Debug Info**\n"
            f"Bot path: `{bot_problem_path}`\n"
            f"Joy path: `{joy_cmds_problem_path}`\n"
            f"Same path: {bot_problem_path == joy_cmds_problem_path}\n"
            f"Bot file exists: {bot_exists} ({bot_content})\n"
            f"Joy file exists: {joy_exists} ({joy_content})\n"
            f"CWD: `{os.getcwd()}`"
        )
        
        await ctx.reply(debug_info, mention_author=False)

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
            
            # Also clean up expired popup messages (older than 30 minutes)
            expired_users = []
            for user_id, user_messages in user_popup_messages.items():
                valid_messages = {}
                
                # Check main message
                if "main_message" in user_messages:
                    try:
                        message = user_messages["main_message"]
                        if hasattr(message, 'created_at'):
                            message_age = (current_time - message.created_at.timestamp())
                            if message_age < 1800:  # 30 minutes
                                valid_messages["main_message"] = message
                    except:
                        pass
                
                # Check last popup message
                if "last_popup" in user_messages:
                    try:
                        message = user_messages["last_popup"]
                        if hasattr(message, 'created_at'):
                            message_age = (current_time - message.created_at.timestamp())
                            if message_age < 1800:  # 30 minutes
                                valid_messages["last_popup"] = message
                    except:
                        pass
                
                if valid_messages:
                    user_popup_messages[user_id] = valid_messages
                else:
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del user_popup_messages[user_id]
                logger.info(f"Cleaned up expired popup messages for user: {user_id}")
            
            await asyncio.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Error in session cleanup: {e}")
            await asyncio.sleep(60)
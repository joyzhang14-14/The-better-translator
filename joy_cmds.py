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
# Use absolute path for problems.json to ensure it's in the current working directory
PROBLEM_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "problems.json"))

# Global storage for pending interactions
pending_glossary_sessions: Dict[str, Dict[str, Any]] = {}

# Global storage for tracking user's popup messages that should be cleaned up
# Structure: {user_id: {"last_popup": message_object, "main_message": message_object}}
user_popup_messages: Dict[int, Dict[str, discord.Message]] = {}

def _save_json(path, data):
    try:
        # DEBUG: Log the data being saved
        logger.info(f"SAVE_DEBUG: About to save {len(data) if isinstance(data, list) else 'non-list'} items to {path}")
        logger.info(f"SAVE_DEBUG: Data preview: {str(data)[:200]}")
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        # Create a temporary file first, then rename to ensure atomic write
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # DEBUG: Verify temp file content
        if os.path.exists(temp_path):
            temp_size = os.path.getsize(temp_path)
            logger.info(f"SAVE_DEBUG: Temp file {temp_path} created with size {temp_size}")
            with open(temp_path, 'r', encoding='utf-8') as f:
                temp_content = f.read()
                logger.info(f"SAVE_DEBUG: Temp content preview: {temp_content[:100]}")
        
        # Atomic rename
        os.replace(temp_path, path)
        
        # DEBUG: Verify final file
        if os.path.exists(path):
            final_size = os.path.getsize(path)
            logger.info(f"SAVE_DEBUG: Final file {path} has size {final_size}")
            with open(path, 'r', encoding='utf-8') as f:
                final_content = f.read()
                logger.info(f"SAVE_DEBUG: Final content preview: {final_content[:100]}")
        
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
        # DEBUG: Log load operation
        if 'problems.json' in path:
            logger.info(f"LOAD_DEBUG: Loading from {path}")
            if os.path.exists(path):
                file_size = os.path.getsize(path)
                logger.info(f"LOAD_DEBUG: File exists with size {file_size}")
            else:
                logger.info(f"LOAD_DEBUG: File does not exist")
        
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            
            # DEBUG: Log content for problem.json
            if 'problems.json' in path:
                logger.info(f"LOAD_DEBUG: Raw content: {repr(txt[:100])}")
            
            result = json.loads(txt) if txt else fallback
            
            # DEBUG: Log result for problem.json
            if 'problems.json' in path:
                logger.info(f"LOAD_DEBUG: Parsed {len(result) if isinstance(result, list) else 'non-list'} items")
            
            return result
    except Exception as e:
        if 'problems.json' in path:
            logger.info(f"LOAD_DEBUG: Exception loading {path}: {e}, returning fallback")
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
    """Clean up ALL popup messages for immediate deletion"""
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
    
    # Also delete the main message if it exists (for complete cleanup)
    if "main_message" in user_messages:
        try:
            main_message = user_messages["main_message"]
            await main_message.delete()
            logger.info(f"Deleted main message for user {user_id}: {main_message.content[:50] if main_message.content else 'No content'}...")
            del user_messages["main_message"]
        except Exception as e:
            logger.warning(f"Failed to delete main message: {e}")
            # Remove the reference even if deletion failed
            if "main_message" in user_messages:
                del user_messages["main_message"]

async def _cleanup_popup_only(user_id: int):
    """Clean up only popup messages, preserve main menu"""
    if user_id not in user_popup_messages:
        return
    
    user_messages = user_popup_messages[user_id]
    
    # Delete the last popup message if it exists, but keep main_message
    if "last_popup" in user_messages:
        try:
            last_popup = user_messages["last_popup"]
            await last_popup.delete()
            logger.info(f"Deleted popup message for user {user_id}: {last_popup.content[:50] if last_popup.content else 'No content'}...")
            del user_messages["last_popup"]
        except Exception as e:
            logger.warning(f"Failed to delete popup message: {e}")
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

class UserManagementView(discord.ui.View):
    def __init__(self, guild_id: str, *, timeout=600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
    
    @discord.ui.button(label="1. 添加白名单用户 Add User", style=discord.ButtonStyle.green)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        # Show user selection modal
        modal = AddUserModal(self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="2. 查看白名单用户 List Users", style=discord.ButtonStyle.secondary)
    async def list_users(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        whitelisted_users = admin_config.get("allowed_user_ids", [])
        
        if not whitelisted_users:
            await interaction.response.send_message("📋 暂无白名单用户 No whitelisted users", ephemeral=True)
        else:
            user_list = []
            for user_id in whitelisted_users:
                try:
                    user = interaction.guild.get_member(user_id)
                    name = user.display_name if user else f"Unknown User ({user_id})"
                    user_list.append(f"• {name} (ID: {user_id})")
                except:
                    user_list.append(f"• Unknown User (ID: {user_id})")
            
            result = "**白名单用户 Whitelisted Users:**\n" + "\n".join(user_list)
            if len(result) > 1900:  # Discord message limit
                result = result[:1900] + "...\n(消息过长已截断 Message truncated)"
            
            await interaction.response.send_message(result, ephemeral=True)
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="3. 删除白名单用户 Remove User", style=discord.ButtonStyle.danger)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        whitelisted_users = admin_config.get("allowed_user_ids", [])
        
        if not whitelisted_users:
            await interaction.response.send_message("❌ 暂无白名单用户可删除 No whitelisted users to remove", ephemeral=True)
            # Track this popup message for cleanup
            try:
                response_message = await interaction.original_response()
                _track_popup_message(interaction.user.id, response_message)
            except Exception as e:
                logger.warning(f"Failed to track popup message: {e}")
            return
        
        # Create user selection dropdown
        view = RemoveUserView(self.guild_id, whitelisted_users, interaction.guild)
        await interaction.response.send_message(
            "🗑️ 选择要删除的白名单用户 Select user to remove from whitelist:",
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
        for item in self.children:
            item.disabled = True

class AddUserModal(discord.ui.Modal, title="添加白名单用户 Add Whitelisted User"):
    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id
    
    user_mention = discord.ui.TextInput(
        label="用户提及或ID User mention or ID",
        style=discord.TextStyle.short,
        placeholder="例如: @username 或 1234567890",
        max_length=100,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_input = self.user_mention.value.strip()
            user_id = None
            
            # Try to extract user ID from mention or direct ID
            if user_input.startswith('<@') and user_input.endswith('>'):
                # Extract from mention format <@!1234567890> or <@1234567890>
                user_id = int(user_input.replace('<@!', '').replace('<@', '').replace('>', ''))
            else:
                # Try direct ID
                user_id = int(user_input)
            
            # Verify user exists in the guild
            user = interaction.guild.get_member(user_id)
            if not user:
                await interaction.response.send_message("❌ 用户不在此服务器中 User not found in this server", ephemeral=True)
                return
            
            # Add to whitelist
            config = _load_json_or(CONFIG_PATH, {})
            admin_config = _ensure_admin_block(config, self.guild_id)
            current_users = set(admin_config.get("allowed_user_ids", []))
            
            if user_id in current_users:
                await interaction.response.send_message(f"⚠️ {user.display_name} 已在白名单中 already in whitelist", ephemeral=True)
                return
            
            current_users.add(user_id)
            admin_config["allowed_user_ids"] = list(current_users)
            _save_json(CONFIG_PATH, config)
            
            await interaction.response.send_message(f"✅ 已添加 {user.display_name} 到白名单 Added to whitelist", ephemeral=True)
            logger.info(f"Added user {user.display_name} ({user_id}) to whitelist for guild {self.guild_id}")
            
        except ValueError:
            await interaction.response.send_message("❌ 无效的用户ID格式 Invalid user ID format", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to add user to whitelist: {e}")
            await interaction.response.send_message("❌ 添加失败 Add failed", ephemeral=True)

class RemoveUserView(discord.ui.View):
    def __init__(self, guild_id: str, whitelisted_users: List[int], guild, *, timeout=600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        
        # Create dropdown with user options
        options = []
        count = 0
        for user_id in whitelisted_users:
            count += 1
            try:
                user = guild.get_member(user_id)
                name = user.display_name if user else f"Unknown User"
                label = f"{name}"
                # Truncate label if too long
                if len(label) > 80:
                    label = label[:77] + "..."
                
                description = f"ID: {user_id}"
                options.append(discord.SelectOption(
                    label=label,
                    value=str(user_id),
                    description=description,
                ))
            except:
                options.append(discord.SelectOption(
                    label=f"Unknown User",
                    value=str(user_id),
                    description=f"ID: {user_id}",
                ))
            
            # Discord dropdown limit is 25 options
            if count >= 25:
                break
        
        if options:
            select = RemoveUserSelect(self.guild_id, options)
            self.add_item(select)
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class RemoveUserSelect(discord.ui.Select):
    def __init__(self, guild_id: str, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="选择要删除的用户... Select user to remove...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        selected_user_id = int(self.values[0])
        
        try:
            # Get user info for confirmation
            user = interaction.guild.get_member(selected_user_id)
            user_name = user.display_name if user else f"Unknown User ({selected_user_id})"
            
            # Remove from whitelist
            config = _load_json_or(CONFIG_PATH, {})
            admin_config = _ensure_admin_block(config, self.guild_id)
            current_users = set(admin_config.get("allowed_user_ids", []))
            
            if selected_user_id not in current_users:
                await interaction.response.send_message("❌ 用户不在白名单中 User not in whitelist", ephemeral=True)
                return
            
            current_users.remove(selected_user_id)
            admin_config["allowed_user_ids"] = list(current_users)
            _save_json(CONFIG_PATH, config)
            
            await interaction.response.send_message(f"✅ 已从白名单移除 {user_name} Removed from whitelist", ephemeral=True)
            logger.info(f"Removed user {user_name} ({selected_user_id}) from whitelist for guild {self.guild_id}")
            
            # Track this popup message for cleanup
            try:
                response_message = await interaction.original_response()
                _track_popup_message(interaction.user.id, response_message)
            except Exception as e:
                logger.warning(f"Failed to track popup message: {e}")
            
        except Exception as e:
            logger.error(f"Failed to remove user from whitelist: {e}")
            await interaction.response.send_message("❌ 删除失败 Remove failed", ephemeral=True)

class RoleManagementView(discord.ui.View):
    def __init__(self, guild_id: str, *, timeout=600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
    
    @discord.ui.button(label="1. 添加白名单角色 Add Role", style=discord.ButtonStyle.green)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        # Show role selection modal
        modal = AddRoleModal(self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="2. 查看白名单角色 List Roles", style=discord.ButtonStyle.secondary)
    async def list_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        whitelisted_roles = admin_config.get("allowed_role_ids", [])
        
        if not whitelisted_roles:
            await interaction.response.send_message("📋 暂无白名单角色 No whitelisted roles", ephemeral=True)
        else:
            role_list = []
            for role_id in whitelisted_roles:
                try:
                    role = interaction.guild.get_role(role_id)
                    name = role.name if role else f"Unknown Role ({role_id})"
                    role_list.append(f"• {name} (ID: {role_id})")
                except:
                    role_list.append(f"• Unknown Role (ID: {role_id})")
            
            result = "**白名单角色 Whitelisted Roles:**\n" + "\n".join(role_list)
            if len(result) > 1900:  # Discord message limit
                result = result[:1900] + "...\n(消息过长已截断 Message truncated)"
            
            await interaction.response.send_message(result, ephemeral=True)
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="3. 删除白名单角色 Remove Role", style=discord.ButtonStyle.danger)
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        whitelisted_roles = admin_config.get("allowed_role_ids", [])
        
        if not whitelisted_roles:
            await interaction.response.send_message("❌ 暂无白名单角色可删除 No whitelisted roles to remove", ephemeral=True)
            # Track this popup message for cleanup
            try:
                response_message = await interaction.original_response()
                _track_popup_message(interaction.user.id, response_message)
            except Exception as e:
                logger.warning(f"Failed to track popup message: {e}")
            return
        
        # Create role selection dropdown
        view = RemoveRoleView(self.guild_id, whitelisted_roles, interaction.guild)
        await interaction.response.send_message(
            "🗑️ 选择要删除的白名单角色 Select role to remove from whitelist:",
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
        for item in self.children:
            item.disabled = True

class AddRoleModal(discord.ui.Modal, title="添加白名单角色 Add Whitelisted Role"):
    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id
    
    role_mention = discord.ui.TextInput(
        label="角色提及或ID Role mention or ID",
        style=discord.TextStyle.short,
        placeholder="例如: @RoleName 或 1234567890",
        max_length=100,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_input = self.role_mention.value.strip()
            role_id = None
            
            # Try to extract role ID from mention or direct ID
            if role_input.startswith('<@&') and role_input.endswith('>'):
                # Extract from mention format <@&1234567890>
                role_id = int(role_input.replace('<@&', '').replace('>', ''))
            else:
                # Try direct ID
                role_id = int(role_input)
            
            # Verify role exists in the guild
            role = interaction.guild.get_role(role_id)
            if not role:
                await interaction.response.send_message("❌ 角色不在此服务器中 Role not found in this server", ephemeral=True)
                return
            
            # Add to whitelist
            config = _load_json_or(CONFIG_PATH, {})
            admin_config = _ensure_admin_block(config, self.guild_id)
            current_roles = set(admin_config.get("allowed_role_ids", []))
            
            if role_id in current_roles:
                await interaction.response.send_message(f"⚠️ {role.name} 已在白名单中 already in whitelist", ephemeral=True)
                return
            
            current_roles.add(role_id)
            admin_config["allowed_role_ids"] = list(current_roles)
            _save_json(CONFIG_PATH, config)
            
            await interaction.response.send_message(f"✅ 已添加 {role.name} 到白名单 Added to whitelist", ephemeral=True)
            logger.info(f"Added role {role.name} ({role_id}) to whitelist for guild {self.guild_id}")
            
        except ValueError:
            await interaction.response.send_message("❌ 无效的角色ID格式 Invalid role ID format", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to add role to whitelist: {e}")
            await interaction.response.send_message("❌ 添加失败 Add failed", ephemeral=True)

class RemoveRoleView(discord.ui.View):
    def __init__(self, guild_id: str, whitelisted_roles: List[int], guild, *, timeout=600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        
        # Create dropdown with role options
        options = []
        count = 0
        for role_id in whitelisted_roles:
            count += 1
            try:
                role = guild.get_role(role_id)
                name = role.name if role else f"Unknown Role"
                label = f"{name}"
                # Truncate label if too long
                if len(label) > 80:
                    label = label[:77] + "..."
                
                description = f"ID: {role_id}"
                options.append(discord.SelectOption(
                    label=label,
                    value=str(role_id),
                    description=description,
                ))
            except:
                options.append(discord.SelectOption(
                    label=f"Unknown Role",
                    value=str(role_id),
                    description=f"ID: {role_id}",
                ))
            
            # Discord dropdown limit is 25 options
            if count >= 25:
                break
        
        if options:
            select = RemoveRoleSelect(self.guild_id, options)
            self.add_item(select)
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class RemoveRoleSelect(discord.ui.Select):
    def __init__(self, guild_id: str, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="选择要删除的角色... Select role to remove...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        selected_role_id = int(self.values[0])
        
        try:
            # Get role info for confirmation
            role = interaction.guild.get_role(selected_role_id)
            role_name = role.name if role else f"Unknown Role ({selected_role_id})"
            
            # Remove from whitelist
            config = _load_json_or(CONFIG_PATH, {})
            admin_config = _ensure_admin_block(config, self.guild_id)
            current_roles = set(admin_config.get("allowed_role_ids", []))
            
            if selected_role_id not in current_roles:
                await interaction.response.send_message("❌ 角色不在白名单中 Role not in whitelist", ephemeral=True)
                return
            
            current_roles.remove(selected_role_id)
            admin_config["allowed_role_ids"] = list(current_roles)
            _save_json(CONFIG_PATH, config)
            
            await interaction.response.send_message(f"✅ 已从白名单移除 {role_name} Removed from whitelist", ephemeral=True)
            logger.info(f"Removed role {role_name} ({selected_role_id}) from whitelist for guild {self.guild_id}")
            
            # Track this popup message for cleanup
            try:
                response_message = await interaction.original_response()
                _track_popup_message(interaction.user.id, response_message)
            except Exception as e:
                logger.warning(f"Failed to track popup message: {e}")
            
        except Exception as e:
            logger.error(f"Failed to remove role from whitelist: {e}")
            await interaction.response.send_message("❌ 删除失败 Remove failed", ephemeral=True)

class PermissionMenuView(discord.ui.View):
    def __init__(self, guild_id: str, *, timeout=600):  # 10 minutes timeout
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
    
    @discord.ui.button(label="1. 白名单用户 Whitelisted Users", style=discord.ButtonStyle.secondary)
    async def manage_users(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        # Show user management submenu
        view = UserManagementView(self.guild_id)
        await interaction.response.send_message(
            "**白名单用户管理 Whitelisted User Management**\n\n"
            "请选择操作 Please select an operation:",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="2. 白名单角色 Whitelisted Roles", style=discord.ButtonStyle.secondary)
    async def manage_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        # Show role management submenu
        view = RoleManagementView(self.guild_id)
        await interaction.response.send_message(
            "**白名单角色管理 Whitelisted Role Management**\n\n"
            "请选择操作 Please select an operation:",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="3. 权限模式 Permission Mode", style=discord.ButtonStyle.danger)
    async def manage_permission_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        require_manage_guild = admin_config.get("require_manage_guild", True)
        
        view = PermissionModeToggleView(self.guild_id)
        status_text = "开启 ON" if require_manage_guild else "关闭 OFF"
        await interaction.response.send_message(
            f"**权限模式设置 Permission Mode Settings**\n\n"
            f"**当前状态 Current Status**: {status_text}\n\n"
            f"**说明 Description**:\n"
            f"开启 ON: 需要管理服务器权限或在白名单中才能使用命令\n"
            f"关闭 OFF: 所有用户都可以使用命令\n\n"
            f"Enable ON: Requires server management permissions or whitelist to use commands\n"
            f"Disable OFF: All users can use commands",
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
        for item in self.children:
            item.disabled = True

class PermissionModeToggleView(discord.ui.View):
    def __init__(self, guild_id: str, *, timeout=300):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
    
    @discord.ui.button(label="开启权限限制 Enable Permission Restriction", style=discord.ButtonStyle.danger)
    async def enable_restriction(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        admin_config["require_manage_guild"] = True
        _save_json(CONFIG_PATH, config)
        
        await interaction.response.send_message(
            "✅ **权限限制已开启 Permission Restriction Enabled**\n\n"
            "现在只有服主、白名单用户或拥有管理服务器权限的用户才能使用bot命令\n"
            "Now only server owner, whitelisted users, or users with server management permissions can use bot commands",
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="关闭权限限制 Disable Permission Restriction", style=discord.ButtonStyle.green)
    async def disable_restriction(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        admin_config = _ensure_admin_block(config, self.guild_id)
        admin_config["require_manage_guild"] = False
        _save_json(CONFIG_PATH, config)
        
        await interaction.response.send_message(
            "✅ **权限限制已关闭 Permission Restriction Disabled**\n\n"
            "现在所有用户都可以使用bot命令\n"
            "Now all users can use bot commands",
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

class GlossaryMenuView(discord.ui.View):
    def __init__(self, *, timeout=600):  # 10 minutes timeout
        super().__init__(timeout=timeout)
    
    @discord.ui.button(label="1. 添加术语 Add Terms", style=discord.ButtonStyle.green)
    async def add_term(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
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
            "添加术语为强制替换还是选择性替换\nIs adding a term a mandatory or optional replacement?",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    @discord.ui.button(label="2. 查看术语 List Terms", style=discord.ButtonStyle.secondary)
    async def list_terms(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
        guild_id = str(interaction.guild.id)
        glossaries = _load_json_or(GLOSSARIES_PATH, {})
        guild_glossaries = glossaries.get(guild_id, {})
        
        if not guild_glossaries:
            await interaction.response.send_message("📋 本群组暂无术语 No terms in this guild", ephemeral=True)
        else:
            # Format glossaries list
            lines = ["📋 **术语列表 Terms List**\n"]
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
    
    @discord.ui.button(label="3. 删除术语 Delete Terms", style=discord.ButtonStyle.danger)
    async def delete_terms(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
        guild_id = str(interaction.guild.id)
        glossaries = _load_json_or(GLOSSARIES_PATH, {})
        guild_glossaries = glossaries.get(guild_id, {})
        
        if not guild_glossaries:
            await interaction.response.send_message("❌ 本群组暂无术语可删除 No terms to delete in this guild", ephemeral=True)
            # Track this popup message for cleanup
            try:
                response_message = await interaction.original_response()
                _track_popup_message(interaction.user.id, response_message)
            except Exception as e:
                logger.warning(f"Failed to track popup message: {e}")
            return
        
        # Create selection dropdown
        view = DeleteGlossaryView(guild_id, guild_glossaries)
        await interaction.response.send_message(
            "🗑️ 选择要删除的术语 Select term to delete:",
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
        for item in self.children:
            item.disabled = True

class ErrorSelectionView(discord.ui.View):
    def __init__(self, guild_id: str, user_id: int, is_owner: bool, *, timeout=600):  # 10 minutes timeout
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.user_id = user_id
        self.is_owner = is_owner
        self.message = None  # Will be set after the message is sent
        
        # Check if user is whitelisted
        config = _load_json_or(CONFIG_PATH, {})
        self.is_whitelisted = _is_whitelist_user(config, int(guild_id), user_id)
        self.has_admin_access = is_owner or self.is_whitelisted
        
        # Add buttons dynamically based on permissions
        self._add_buttons()
    
    def _add_buttons(self):
        # Button 1: Always visible - Report bug
        report_button = discord.ui.Button(
            label="1. 报告翻译逻辑错误 report bot logical bug",
            style=discord.ButtonStyle.red
        )
        report_button.callback = self.report_bug
        self.add_item(report_button)
        
        # Button 2: Always visible - Glossary
        glossary_button = discord.ui.Button(
            label="2. 术语表 Glossary",
            style=discord.ButtonStyle.blurple
        )
        glossary_button.callback = self.glossary_menu
        self.add_item(glossary_button)
        
        # Button 3: Admin only - Term Detection Settings
        if self.has_admin_access:
            term_detection_button = discord.ui.Button(
                label="3. 术语检测设置 Term Detection Settings",
                style=discord.ButtonStyle.secondary
            )
            term_detection_button.callback = self.toggle_term_detection
            self.add_item(term_detection_button)
        
        # Button 4: Admin only - Permission Settings
        if self.has_admin_access:
            permission_button = discord.ui.Button(
                label="4. 权限设置 Permission Settings",
                style=discord.ButtonStyle.danger
            )
            permission_button.callback = self.permission_settings
            self.add_item(permission_button)
    
    async def report_bug(self, interaction: discord.Interaction):
        # Clean up old popups before showing modal
        await _cleanup_popup_only(interaction.user.id)
        
        # Create and send the problem report modal, don't pass main message for deletion
        modal = ProblemReportModal(None)  # Don't delete main message
        await interaction.response.send_modal(modal)
    
    async def glossary_menu(self, interaction: discord.Interaction):
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
        # Show glossary management submenu
        view = GlossaryMenuView()
        await interaction.response.send_message(
            "**术语表管理 Glossary Management**\n\n"
            "请选择操作 Please select an operation:",
            view=view,
            ephemeral=True
        )
        
        # Track this popup message for cleanup
        try:
            response_message = await interaction.original_response()
            _track_popup_message(interaction.user.id, response_message)
        except Exception as e:
            logger.warning(f"Failed to track popup message: {e}")
    
    async def toggle_term_detection(self, interaction: discord.Interaction):
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
        config = _load_json_or(CONFIG_PATH, {})
        
        # Get current term detection status (default: enabled)
        guild_config = config.get("guilds", {}).get(self.guild_id, {})
        current_status = guild_config.get("glossary_enabled", True)
        
        logger.info(f"TERM_DEBUG: Guild {self.guild_id} term detection status: {current_status}")
        
        # Create toggle view
        view = GlossaryToggleView(self.guild_id)
        status_text = "启用 Enabled" if current_status else "禁用 Disabled"
        await interaction.response.send_message(
            f"**术语检测设置 Term Detection Settings**\n\n"
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
    
    async def permission_settings(self, interaction: discord.Interaction):
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
        # Show permission management submenu
        view = PermissionMenuView(self.guild_id)
        await interaction.response.send_message(
            "**权限设置 Permission Settings**\n\n"
            "请选择操作 Please select an operation:",
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
        
        # Try to delete the main menu message after timeout
        try:
            if self.message:
                await self.message.delete()
                logger.info(f"Main menu message auto-deleted after 10 minutes timeout for user {self.user_id}")
                
                # Remove from tracking
                if self.user_id in user_popup_messages and "main_message" in user_popup_messages[self.user_id]:
                    del user_popup_messages[self.user_id]["main_message"]
        except Exception as e:
            logger.warning(f"Failed to auto-delete main menu message: {e}")

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
        await _cleanup_popup_only(interaction.user.id)
        
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
        await _cleanup_popup_only(interaction.user.id)
        
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
        # Save problem report to problems.json with enhanced debugging
        try:
            logger.info(f"=== PROBLEM REPORT DEBUG START ===")
            logger.info(f"Starting to save problem report from user {interaction.user.display_name}")
            logger.info(f"PROBLEM_PATH: {PROBLEM_PATH}")
            logger.info(f"Current working directory: {os.getcwd()}")
            logger.info(f"PROBLEM_PATH exists: {os.path.exists(PROBLEM_PATH)}")
            logger.info(f"PROBLEM_PATH is file: {os.path.isfile(PROBLEM_PATH)}")
            logger.info(f"Directory of PROBLEM_PATH: {os.path.dirname(PROBLEM_PATH)}")
            logger.info(f"Directory exists: {os.path.exists(os.path.dirname(PROBLEM_PATH))}")
            
            # Check file permissions
            try:
                with open(PROBLEM_PATH, 'a') as test_file:
                    pass
                logger.info(f"File is writable: True")
            except Exception as perm_error:
                logger.error(f"File permission error: {perm_error}")
            
            # Load existing problems from cloud storage first, then fallback to local
            try:
                logger.info(f"Attempting to load problems from cloud storage...")
                problems = await storage.load_json("problems", [])
                logger.info(f"Loaded {len(problems)} existing problems from cloud storage")
                
                # If we got data from cloud, also update local file
                if problems:
                    abs_path = os.path.abspath(PROBLEM_PATH)
                    _save_json(abs_path, problems)
                    logger.info(f"Synced {len(problems)} problems to local file")
                    
            except Exception as cloud_error:
                logger.warning(f"Failed to load from cloud storage: {cloud_error}, trying local file")
                problems = _load_json_or(PROBLEM_PATH, [])
                logger.info(f"Loaded {len(problems)} existing problems from local file")
            
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
            
            # FORCE ABSOLUTE PATH to ensure we're writing to the right place
            abs_path = os.path.abspath(PROBLEM_PATH)
            logger.info(f"Using absolute path: {abs_path}")
            
            _save_json(abs_path, problems)
            
            # ALSO save to cloud storage for persistence across deployments
            try:
                logger.info(f"Attempting to save problems to cloud storage...")
                await storage.save_json("problems", problems)
                logger.info(f"Successfully saved problems to cloud storage")
            except Exception as cloud_error:
                logger.warning(f"Failed to save to cloud storage: {cloud_error}")
                # Don't fail the entire operation if cloud save fails
            
            # Verify the save by reading back
            saved_problems = _load_json_or(abs_path, [])
            logger.info(f"Verification: file now contains {len(saved_problems)} problems")
            
            # Additional verification - check file size
            if os.path.exists(abs_path):
                file_size = os.path.getsize(abs_path)
                logger.info(f"Final file size: {file_size} bytes")
                
                # Read and log actual file content
                try:
                    with open(abs_path, 'r', encoding='utf-8') as f:
                        actual_content = f.read()
                        logger.info(f"Actual file content length: {len(actual_content)}")
                        logger.info(f"Actual file content preview: {actual_content[:200]}")
                except Exception as read_error:
                    logger.error(f"Error reading back file: {read_error}")
            else:
                logger.error(f"File does not exist after save attempt!")
            
            await interaction.response.send_message("✅已成功提交 submitted", ephemeral=True)
            logger.info(f"Problem report successfully processed: {problem_entry}")
            logger.info(f"=== PROBLEM REPORT DEBUG END ===")
            
            # Delete the original bot message to clean up interface
            if self.original_message:
                try:
                    await self.original_message.delete()
                    logger.info("Deleted original bot message after problem report submission")
                except Exception as delete_error:
                    logger.warning(f"Failed to delete original message: {delete_error}")
            
        except Exception as e:
            logger.error(f"=== PROBLEM REPORT ERROR ===")
            logger.error(f"Failed to save problem report: {e}")
            logger.error(f"Error type: {type(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            logger.error(f"Current working directory at error: {os.getcwd()}")
            logger.error(f"=== END ERROR ===")
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
        # Clean up old popups before showing new one
        await _cleanup_popup_only(interaction.user.id)
        
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
        # Clean up old popups before showing modal
        await _cleanup_popup_only(interaction.user.id)
        
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
        # Clean up old popups before showing modal
        await _cleanup_popup_only(interaction.user.id)
        
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
    mgmt_cmds = ["!setrequire", "!allowuser", "!denyuser", "!allowrole", "!denyrole", "!bot14", "!sync_problems", "!download_problems", "!clear_problems", "!debug_cloud"]
    _ensure_pt_commands(mgmt_cmds)

    @bot.command(name="bot14")
    async def bot14_command(ctx):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        
        # Clean up old popups before showing main selection
        await _cleanup_old_popups(ctx.author.id)
        
        # Check if user is server owner
        is_owner = ctx.guild.owner_id == ctx.author.id
        
        # Create and send the error selection view with permission check
        # VERSION: v2.3.0 - Update version for major feature additions (Minor +1) or bug fixes (Patch +1)
        # Format: Major.Minor.Patch (e.g., v2.1.0 for new features, v2.0.1 for bug fixes)
        view = ErrorSelectionView(str(ctx.guild.id), ctx.author.id, is_owner)
        message = await ctx.reply(
            "v2.3.0 请选择操作类型 Please select operation type:",
            view=view,
            mention_author=False
        )
        
        # Set the message reference for auto-deletion
        view.message = message
        
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
        bot_problem_path = os.path.abspath(os.path.join(BASE, "problems.json"))
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
    
    @bot.command(name="sync_problems")
    async def sync_problems(ctx):
        # Only allow the specific user (joyzhang14) to use this command
        if ctx.author.id != 1073555366803165245:
            return await ctx.reply("❌此命令仅限特定用户使用 This command is restricted", mention_author=False)
        
        try:
            await ctx.reply("🔄 开始同步问题报告...\nStarting sync of problem reports...", mention_author=False)
            
            # Load problems from cloud storage
            logger.info(f"SYNC: Loading problems from cloud storage...")
            logger.info(f"SYNC: Storage type: {storage.storage_type}")
            logger.info(f"SYNC: Storage URL: {storage.storage_url}")
            logger.info(f"SYNC: Bin ID: {storage.bin_id}")
            
            cloud_problems = await storage.load_json("problems", [])
            logger.info(f"SYNC: Found {len(cloud_problems)} problems in cloud storage")
            
            if not cloud_problems:
                await ctx.send("⚠️ 云存储中没有找到问题报告\nNo problem reports found in cloud storage")
                return
            
            # Save to local file (in container)
            local_path = os.path.abspath(PROBLEM_PATH)
            logger.info(f"SYNC: Saving to local path: {local_path}")
            
            _save_json(local_path, cloud_problems)
            logger.info(f"SYNC: Saved {len(cloud_problems)} problems to local file: {local_path}")
            
            # Verify the save
            saved_problems = _load_json_or(local_path, [])
            logger.info(f"SYNC: Verification - local file now contains {len(saved_problems)} problems")
            
            await ctx.send(f"✅ 已同步 {len(cloud_problems)} 个问题报告到容器本地文件\nSynced {len(cloud_problems)} problem reports to container local file\n\n📍 文件位置 File location: `{local_path}`")
            
        except Exception as e:
            logger.error(f"SYNC: Error syncing problems: {e}")
            import traceback
            logger.error(f"SYNC: Full traceback: {traceback.format_exc()}")
            await ctx.reply(f"❌ 同步失败: {e}\nSync failed: {e}", mention_author=False)
    
    @bot.command(name="download_problems") 
    async def download_problems(ctx):
        # Only allow the specific user (joyzhang14) to use this command
        if ctx.author.id != 1073555366803165245:
            return await ctx.reply("❌此命令仅限特定用户使用 This command is restricted", mention_author=False)
        
        try:
            # Load problems from cloud storage
            logger.info(f"DOWNLOAD: Loading problems from cloud storage...")
            cloud_problems = await storage.load_json("problems", [])
            logger.info(f"DOWNLOAD: Found {len(cloud_problems)} problems in cloud storage")
            
            if not cloud_problems:
                await ctx.reply("⚠️ 云存储中没有找到问题报告\nNo problem reports found in cloud storage", mention_author=False)
                return
                
            # Format problems as JSON
            import json
            problems_json = json.dumps(cloud_problems, ensure_ascii=False, indent=2)
            
            # Create a file and send it
            import io
            file_buffer = io.BytesIO(problems_json.encode('utf-8'))
            
            import discord
            file = discord.File(file_buffer, filename='problems.json')
            
            await ctx.reply(f"📥 下载问题报告文件 ({len(cloud_problems)} 个问题)\nDownloading problem reports file ({len(cloud_problems)} problems)", 
                          file=file, mention_author=False)
            
        except Exception as e:
            logger.error(f"DOWNLOAD: Error downloading problems: {e}")
            import traceback
            logger.error(f"DOWNLOAD: Full traceback: {traceback.format_exc()}")
            await ctx.reply(f"❌ 下载失败: {e}\nDownload failed: {e}", mention_author=False)
    
    @bot.command(name="clear_problems")
    async def clear_problems(ctx):
        # Only allow the specific user (joyzhang14) to use this command
        if ctx.author.id != 1073555366803165245:
            return await ctx.reply("❌此命令仅限特定用户使用 This command is restricted", mention_author=False)
        
        try:
            # Load current problems to show count
            logger.info(f"CLEAR: Loading problems from cloud storage...")
            cloud_problems = await storage.load_json("problems", [])
            logger.info(f"CLEAR: Found {len(cloud_problems)} problems in cloud storage")
            
            if not cloud_problems:
                await ctx.reply("⚠️ 云存储中没有找到问题报告，无需删除\nNo problem reports found in cloud storage, nothing to clear", mention_author=False)
                return
            
            # Ask for confirmation with button
            import discord
            
            class ConfirmClearView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=30)
                    self.confirmed = False
                
                @discord.ui.button(label="确认删除 Confirm Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
                async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != 1073555366803165245:
                        return await interaction.response.send_message("❌只有管理员可以操作 Only admin can operate", ephemeral=True)
                    
                    try:
                        # Clear problems by saving empty list
                        await storage.save_json("problems", [])
                        logger.info(f"CLEAR: Cleared all problems from cloud storage")
                        
                        # Also clear local file
                        local_path = os.path.abspath(PROBLEM_PATH)
                        _save_json(local_path, [])
                        logger.info(f"CLEAR: Cleared local file: {local_path}")
                        
                        await interaction.response.edit_message(
                            content=f"✅ 已成功删除 {len(cloud_problems)} 个问题报告\nSuccessfully deleted {len(cloud_problems)} problem reports",
                            view=None
                        )
                        
                    except Exception as e:
                        logger.error(f"CLEAR: Error clearing problems: {e}")
                        await interaction.response.edit_message(
                            content=f"❌ 删除失败: {e}\nDelete failed: {e}",
                            view=None
                        )
                
                @discord.ui.button(label="取消 Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
                async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != 1073555366803165245:
                        return await interaction.response.send_message("❌只有管理员可以操作 Only admin can operate", ephemeral=True)
                    
                    await interaction.response.edit_message(
                        content="🚫 已取消删除操作\nDelete operation cancelled",
                        view=None
                    )
            
            view = ConfirmClearView()
            await ctx.reply(
                f"⚠️ **危险操作 Dangerous Operation**\n\n"
                f"即将删除 {len(cloud_problems)} 个问题报告\n"
                f"About to delete {len(cloud_problems)} problem reports\n\n"
                f"此操作不可恢复，请确认！\n"
                f"This action cannot be undone, please confirm!",
                view=view,
                mention_author=False
            )
            
        except Exception as e:
            logger.error(f"CLEAR: Error in clear_problems: {e}")
            import traceback
            logger.error(f"CLEAR: Full traceback: {traceback.format_exc()}")
            await ctx.reply(f"❌ 操作失败: {e}\nOperation failed: {e}", mention_author=False)
    
    @bot.command(name="debug_cloud")
    async def debug_cloud(ctx):
        # Only allow the specific user (joyzhang14) to use this command
        if ctx.author.id != 1073555366803165245:
            return await ctx.reply("❌此命令仅限特定用户使用 This command is restricted", mention_author=False)
        
        try:
            # Test cloud storage connection
            logger.info(f"DEBUG_CLOUD: Testing cloud storage connection...")
            
            # Try to load problems from cloud
            try:
                problems = await storage.load_json("problems", [])
                await ctx.reply(f"✅ 云存储连接正常，找到 {len(problems)} 个问题报告\nCloud storage OK, found {len(problems)} problem reports", mention_author=False)
                
                # Show first few problems if any
                if problems:
                    preview = []
                    for i, p in enumerate(problems[:3]):
                        preview.append(f"{i+1}. {p.get('username', 'Unknown')}: {p.get('description', 'No description')[:50]}...")
                    preview_text = "\n".join(preview)
                    if len(problems) > 3:
                        preview_text += f"\n... 还有 {len(problems) - 3} 个 (and {len(problems) - 3} more)"
                    
                    await ctx.send(f"**问题报告预览 Problem Reports Preview:**\n```\n{preview_text}\n```")
                
            except Exception as cloud_error:
                await ctx.reply(f"❌ 云存储连接失败: {cloud_error}\nCloud storage failed: {cloud_error}", mention_author=False)
                
        except Exception as e:
            logger.error(f"DEBUG_CLOUD: Error: {e}")
            await ctx.reply(f"❌ 调试失败: {e}\nDebug failed: {e}", mention_author=False)
    
    @bot.command(name="test_problem")
    async def test_problem(ctx):
        if not _is_whitelist_user(config, ctx.guild.id, ctx.author.id):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        
        try:
            # Test problem report saving directly
            problems = _load_json_or(PROBLEM_PATH, [])
            logger.info(f"TEST: Loaded {len(problems)} existing problems from {PROBLEM_PATH}")
            
            test_entry = {
                "timestamp": time.time(),
                "guild_id": str(ctx.guild.id),
                "user_id": ctx.author.id,
                "username": ctx.author.display_name,
                "description": "TEST PROBLEM REPORT"
            }
            problems.append(test_entry)
            logger.info(f"TEST: Created test entry: {test_entry}")
            
            _save_json(PROBLEM_PATH, problems)
            logger.info(f"TEST: Saved {len(problems)} problems to {PROBLEM_PATH}")
            
            # Verify
            saved_problems = _load_json_or(PROBLEM_PATH, [])
            logger.info(f"TEST: Verification shows {len(saved_problems)} problems")
            
            # Additional debugging: Check file after save
            import os
            file_size = os.path.getsize(PROBLEM_PATH) if os.path.exists(PROBLEM_PATH) else 0
            logger.info(f"TEST: File size after save: {file_size} bytes")
            
            # Read raw file content
            try:
                with open(PROBLEM_PATH, 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                    logger.info(f"TEST: Raw file content: {repr(raw_content[:200])}")
            except Exception as read_error:
                logger.error(f"TEST: Error reading file: {read_error}")
            
            await ctx.reply(f"✅ Test problem report saved. Total problems: {len(saved_problems)}, File size: {file_size} bytes", mention_author=False)
            
        except Exception as e:
            logger.error(f"TEST: Error saving test problem: {e}")
            import traceback
            logger.error(f"TEST: Full traceback: {traceback.format_exc()}")
            await ctx.reply(f"❌ Test failed: {e}", mention_author=False)

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
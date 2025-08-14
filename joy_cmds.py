import os
import json
import logging
from discord.ext import commands
from storage import storage

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
PASSTHROUGH_PATH = os.path.join(os.path.dirname(__file__), "passthrough.json")

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

def register_commands(bot: commands.Bot, config, guild_dicts, dictionary_path, guild_abbrs, abbr_path, can_use):
    mgmt_cmds = ["!setrequire", "!allowuser", "!denyuser", "!allowrole", "!denyrole"]
    _ensure_pt_commands(mgmt_cmds)

    @bot.command(name="addprompt")
    async def addprompt(ctx, zh: str, en: str):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        gid = str(ctx.guild.id)
        d = guild_dicts.setdefault(gid, {})
        if zh in d:
            return await ctx.reply("❗已存在 already exist", mention_author=False)
        d[zh] = en
        # Save to persistent storage
        success = await storage.save_json("dictionary", guild_dicts)
        if success:
            await ctx.reply("✅已添加 added", mention_author=False)
        else:
            await ctx.reply("⚠️已添加但保存失败 added but save failed", mention_author=False)

    @bot.command(name="delprompt")
    async def delprompt(ctx, zh: str):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        gid = str(ctx.guild.id)
        d = guild_dicts.get(gid, {})
        if zh in d:
            d.pop(zh)
            # Save to persistent storage
            success = await storage.save_json("dictionary", guild_dicts)
            if success:
                return await ctx.reply("✅已删除 deleted", mention_author=False)
            else:
                return await ctx.reply("⚠️已删除但保存失败 deleted but save failed", mention_author=False)
        await ctx.reply("❌未找到 cannot find", mention_author=False)

    @bot.command(name="listprompts")
    async def listprompts(ctx):
        gid = str(ctx.guild.id)
        d = guild_dicts.get(gid, {})
        if not d:
            return await ctx.reply("词典为空 prompt list empty", mention_author=False)
        lines = "\n".join(f"{zh} → {en}" for zh, en in d.items())
        await ctx.reply(lines, mention_author=False)

    @bot.command(name="addabbr")
    async def addabbr(ctx, key: str, value: str):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        gid = str(ctx.guild.id)
        d = guild_abbrs.setdefault(gid, {})
        k = key.strip()
        if not k:
            return await ctx.reply("❌无效缩写 invalid", mention_author=False)
        if k in d:
            return await ctx.reply("❗已存在 already exist", mention_author=False)
        d[k] = value
        # Save to persistent storage
        success = await storage.save_json("abbreviations", guild_abbrs)
        if success:
            await ctx.reply("✅已添加 added", mention_author=False)
        else:
            await ctx.reply("⚠️已添加但保存失败 added but save failed", mention_author=False)

    @bot.command(name="delabbr")
    async def delabbr(ctx, key: str):
        if not can_use(ctx.guild, ctx.author):
            return await ctx.reply("❌需要权限 Need permission", mention_author=False)
        gid = str(ctx.guild.id)
        d = guild_abbrs.get(gid, {})
        if key in d:
            d.pop(key)
            # Save to persistent storage
            success = await storage.save_json("abbreviations", guild_abbrs)
            if success:
                return await ctx.reply("✅已删除 deleted", mention_author=False)
            else:
                return await ctx.reply("⚠️已删除但保存失败 deleted but save failed", mention_author=False)
        await ctx.reply("❌未找到 cannot find", mention_author=False)

    @bot.command(name="listabbr")
    async def listabbr(ctx):
        gid = str(ctx.guild.id)
        
        # Debug logging
        logger.info(f"DEBUG listabbr - guild_abbrs type: {type(guild_abbrs)}")
        logger.info(f"DEBUG listabbr - guild_abbrs is: {guild_abbrs}")
        
        guild_specific = guild_abbrs.get(gid, {})
        default_abbrs = guild_abbrs.get("default", {})
        
        logger.info(f"DEBUG listabbr - guild_abbrs keys: {list(guild_abbrs.keys())}")
        logger.info(f"DEBUG listabbr - default_abbrs count: {len(default_abbrs)}")
        logger.info(f"DEBUG listabbr - guild_specific count: {len(guild_specific)}")
        logger.info(f"DEBUG listabbr - default sample: {list(default_abbrs.keys())[:5]}")
        
        lines = []
        
        if default_abbrs:
            lines.append("🌐 **默认缩写 Default Abbreviations:**")
            default_lines = [f"`{k}` → {v}" for k, v in sorted(default_abbrs.items())]
            lines.extend(default_lines[:20])  # Show first 20 to avoid message length limits
            if len(default_abbrs) > 20:
                lines.append(f"... 还有 {len(default_abbrs) - 20} 个 (and {len(default_abbrs) - 20} more)")
        
        if guild_specific:
            lines.append(f"\n🏠 **群组专属 Guild Specific ({ctx.guild.name}):**")
            guild_lines = [f"`{k}` → {v}" for k, v in sorted(guild_specific.items())]
            lines.extend(guild_lines)
        
        if not lines:
            return await ctx.reply("缩写表为空 Abbreviation list empty", mention_author=False)
        
        result = "\n".join(lines)
        if len(result) > 1900:  # Discord message limit
            result = result[:1900] + "...\n(消息过长已截断 Message truncated)"
        
        await ctx.reply(result, mention_author=False)

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

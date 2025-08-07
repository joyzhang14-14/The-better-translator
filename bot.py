#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import re
from typing import Optional, Tuple
from io import BytesIO

import aiohttp
import discord
from discord.ext import commands
import openai
from PIL import Image
import pytesseract

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE, "config.json")
DICTIONARY_PATH = os.path.join(BASE, "dictionary.json")

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_json(CONFIG_PATH)
guild_dicts = load_json(DICTIONARY_PATH)

def find_custom_prompt(text: str, m: dict) -> Tuple[Optional[str], bool, bool]:
    for k in m:
        if text == k:
            return k, True, True
    for k in m:
        if k in text:
            return k, False, True
    return None, False, False

class TranslatorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        openai.api_key = config["openai_key"]
        self.session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        await super().close()

    async def detect_language(self, text: str) -> str:
        prompt = (
            "Identify whether this is Chinese, English, or meaningless. "
            "Reply with exactly one: Chinese, English, meaningless.\n\n"
            + text
        )
        try:
            r = await openai.ChatCompletion.acreate(
                model="gpt-3.5-turbo",
                messages=[{"role":"user","content":prompt}],
                max_tokens=1, temperature=0.0
            )
            ans = r.choices[0].message.content.lower()
            if "chinese" in ans or "中文" in ans:
                return "Chinese"
            if "english" in ans or "英文" in ans:
                return "English"
        except:
            pass
        return "meaningless"

    async def translate_text(self, text: str, direction: str, custom_map: dict) -> str:
        if direction == "en_to_zh":
            inv = {v:k for k,v in custom_map.items()}
            mapping = "; ".join(f"{eng}->{chi}" for eng,chi in inv.items())
            prompt = (
            "把这段话翻译成中文：\n"
            f"{text}\n"
            "如果这段话是脏话，返回：（脏话）\n"
            "如果这段话翻译不出来，返回：/"
        )
        else:
            prompt = (
            "Translate the following to English:\n"
            f"{text}\n"
            "If it's swearing, return: (swearing words)\n"
            "If cannot translate, return: /"
        )
        try:
            r = await openai.ChatCompletion.acreate(
                model="gpt-3.5-turbo",
                messages=[{"role":"user","content":prompt}],
                temperature=0.2
            )
            return r.choices[0].message.content.strip()
        except:
            return "/"

    async def is_pass_through(self, msg: discord.Message) -> bool:
        t = msg.content.strip()
        return not (t and re.search(r"[A-Za-z\u4e00-\u9fff]", t))

    async def send_via_webhook(self, url: str, content: str, msg: discord.Message, *, lang: str):
        if not self.session:
            raise RuntimeError()
        gid = str(msg.guild.id)
        cm = guild_dicts.get(gid, {})
        prefix = None
        ref = msg.reference
        if ref and isinstance(ref.resolved, discord.Message):
            orig = ref.resolved
            name = orig.author.display_name
            jump = orig.jump_url
            orig_lang = await self.detect_language(orig.content or "")
            if lang == "Chinese":
                tr = (await self.translate_text(orig.content or "", "en_to_zh", cm)) if orig_lang=="English" else orig.content or ""
                prefix = f"[回复 @{name}：{tr}]({jump})"
            else:
                tr = (await self.translate_text(orig.content or "", "zh_to_en", cm)) if orig_lang=="Chinese" else orig.content or ""
                prefix = f"[reply @{name}：{tr}]({jump})"
        final = content
        if prefix:
            final = prefix + "\n" + final

        # OCR for images
        ocr_texts = []
        files = []
        for att in msg.attachments:
            try:
                data = await att.read()
                files.append(discord.File(fp=BytesIO(data), filename=att.filename))
                img = Image.open(BytesIO(data))
                text = pytesseract.image_to_string(img, lang="chi_sim+eng").strip()
                if text:
                    ocr_lang = await self.detect_language(text)
                    if ocr_lang == "Chinese":
                        ocr_tr = await self.translate_text(text, "zh_to_en", cm)
                        label = "Image text translation: " if lang=="English" else "图像文字翻译: "
                    elif ocr_lang == "English":
                        ocr_tr = await self.translate_text(text, "en_to_zh", cm)
                        label = "Image text translation: " if lang=="English" else "图像文字翻译: "
                    else:
                        ocr_tr = ""
                    if ocr_tr and ocr_tr != "/":
                        ocr_texts.append(label + ocr_tr)
            except:
                pass

        for o in ocr_texts:
            final += "\n" + o

        try:
            wh = discord.Webhook.from_url(url, session=self.session)
            await wh.send(
                content=final or None,
                username=msg.author.display_name,
                avatar_url=(msg.author.avatar.url if msg.author.avatar else None),
                files=files,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as e:
            logger.exception(e)

    async def on_message(self, msg: discord.Message):
        if msg.author.bot or msg.webhook_id or not msg.guild:
            return
        await self.process_commands(msg)
        gid = str(msg.guild.id)
        cfg = config["guilds"].get(gid)
        if not cfg:
            return
        is_en = msg.channel.id == cfg["en_channel_id"]
        is_zh = msg.channel.id == cfg["zh_channel_id"]
        if not (is_en or is_zh):
            return
        cm = guild_dicts.get(gid, {})
        if await self.is_pass_through(msg):
            tgt = "Chinese" if is_en else "English"
            u = cfg["zh_webhook_url"] if is_en else cfg["en_webhook_url"]
            await self.send_via_webhook(u, msg.content, msg, lang=tgt)
            return
        txt = msg.content.strip()
        lang = await self.detect_language(txt)
        key, _, contains = find_custom_prompt(txt, cm)
        if is_en:
            if lang == "English":
                tr = cm[key] if contains and key else await self.translate_text(txt, "en_to_zh", cm)
                await self.send_via_webhook(cfg["zh_webhook_url"], tr, msg, lang="Chinese")
            elif lang == "Chinese":
                await self.send_via_webhook(cfg["zh_webhook_url"], txt, msg, lang="Chinese")
                tr = cm[key] if contains and key else await self.translate_text(txt, "zh_to_en", cm)
                await self.send_via_webhook(cfg["en_webhook_url"], tr, msg, lang="English")
            else:
                await self.send_via_webhook(cfg["zh_webhook_url"], txt, msg, lang="Chinese")
        else:
            if lang == "Chinese":
                tr = cm[key] if contains and key else await self.translate_text(txt, "zh_to_en", cm)
                await self.send_via_webhook(cfg["en_webhook_url"], tr, msg, lang="English")
            elif lang == "English":
                await self.send_via_webhook(cfg["en_webhook_url"], txt, msg, lang="English")
                tr = await self.translate_text(txt, "en_to_zh", cm)
                await self.send_via_webhook(cfg["zh_webhook_url"], tr, msg, lang="Chinese")
            else:
                await self.send_via_webhook(cfg["en_webhook_url"], txt, msg, lang="English")

def main():
    if "YOUR_DISCORD" in config.get("discord_token", ""):
        raise RuntimeError("请在 config.json 填写 Discord Bot Token")
    if "YOUR_OPENAI" in config.get("openai_key", ""):
        raise RuntimeError("请在 config.json 填写 OpenAI API Key")
    bot = TranslatorBot()

    @bot.command(name="addprompt")
    async def addprompt(ctx, zh: str, en: str):
        gid = str(ctx.guild.id)
        d = guild_dicts.setdefault(gid, {})
        if zh in d:
            return await ctx.reply("❗已存在", mention_author=False)
        d[zh] = en
        with open(DICTIONARY_PATH, "w", encoding="utf-8") as f:
            json.dump(guild_dicts, f, ensure_ascii=False, indent=2)
        await ctx.reply("✅已添加", mention_author=False)

    @bot.command(name="delprompt")
    async def delprompt(ctx, zh: str):
        gid = str(ctx.guild.id)
        d = guild_dicts.get(gid, {})
        if zh in d:
            d.pop(zh)
            with open(DICTIONARY_PATH, "w", encoding="utf-8") as f:
                json.dump(guild_dicts, f, ensure_ascii=False, indent=2)
            return await ctx.reply("✅已删除", mention_author=False)
        await ctx.reply("❌未找到", mention_author=False)

    @bot.command(name="listprompts")
    async def listprompts(ctx):
        gid = str(ctx.guild.id)
        d = guild_dicts.get(gid, {})
        if not d:
            return await ctx.reply("词典为空", mention_author=False)
        lines = "\n".join(f"{zh} → {en}" for zh, en in d.items())
        await ctx.reply(lines, mention_author=False)

    bot.run(config["discord_token"])

if __name__ == "__main__":
    main()

import discord
import openai
import json, re, requests
from discord import AllowedMentions

# ————————— 环境检查 —————————
print("🔍 openai 库版本：", openai.__version__)
print("🔍 discord.py 版本：", discord.__version__)

# ————————— 加载配置 —————————
with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)
with open("dictionary.json", "r", encoding="utf-8") as f:
    dictionary = json.load(f)

openai.api_key = cfg["openai_key"]
TOKEN     = cfg["discord_token"]
ZH_CH_ID  = cfg["zh_channel_id"]
EN_CH_ID  = cfg["en_channel_id"]
ZH_WH_URL = cfg["zh_webhook_url"]
EN_WH_URL = cfg["en_webhook_url"]

# ————————— 初始化 Bot —————————
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
NO_PING = AllowedMentions(users=False)

# ————————— 工具函数 —————————
def contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))

async def gpt_translate(text: str, system_prompt: str) -> str:
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role":"system", "content": system_prompt},
                {"role":"user",   "content": text}
            ],
            temperature=0.2
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("💥 GPT 异常：", e)
        return ""

# ————————— 核心：翻译 + 词典 + 二次翻译 + Webhook + Reply —————————
async def handle_translation(msg: discord.Message, src: int, dst: int, en2zh: bool):
    # 跳过 Bot 或 Webhook
    if msg.author.bot or msg.webhook_id:
        return
    if msg.channel.id != src:
        return

    content = msg.content.strip()
    key = content.lower()

    # 1) 词典优先
    if key in dictionary:
        translated = dictionary[key][1] if en2zh else dictionary[key][0]
    else:
        # 2) 第一次 GPT 翻译
        if en2zh:
            system = "You are a professional translator. Translate English to Chinese succinctly."
        else:
            system = "You are a professional translator. Translate Chinese to English succinctly."
        translated = await gpt_translate(content, system)

    # 3) 如果期望中文输出但结果无中文，则二次翻译
    if en2zh and not contains_chinese(translated):
        translated = await gpt_translate(translated, "You are a translator. Translate English to Chinese succinctly.")

    # 4) 如果期望英文输出但结果含中文，也可类似二次处理（可选）
    if not en2zh and contains_chinese(translated):
         translated = await gpt_translate(translated, "You are a translator. Translate Chinese to English succinctly.")

    # 构造 Reply 引用
    reference = None
    if msg.reference and isinstance(msg.reference.resolved, discord.Message):
        rm = msg.reference.resolved
        reference = {
            "message_id": rm.id,
            "channel_id": rm.channel.id,
            "guild_id":   msg.guild.id,
            "fail_if_not_exists": False
        }

    # 选择 Webhook URL
    webhook_url = EN_WH_URL if dst == EN_CH_ID else ZH_WH_URL

    # 构造 payload
    payload = {
        "content": translated or content,
        "username": msg.author.display_name,
        "avatar_url": msg.author.display_avatar.url,
        "allowed_mentions": {"users": []}
    }
    if reference:
        payload["message_reference"] = reference

    # 发送
    r = requests.post(webhook_url, json=payload)
    if r.status_code not in (200, 204):
        print("❌ Webhook 失败：", r.status_code, r.text)

# ————————— 事件绑定 —————————
@bot.event
async def on_ready():
    print("✅ Bot 已上线：", bot.user)

@bot.event
async def on_message(message: discord.Message):
    # 英文→中文
    await handle_translation(message, EN_CH_ID, ZH_CH_ID, en2zh=True)
    # 中文→英文
    await handle_translation(message, ZH_CH_ID, EN_CH_ID, en2zh=False)

# ————————— 启动 Bot —————————
bot.run(TOKEN)

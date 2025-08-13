# 🚀 Vercel 部署指南 - Discord 翻译机器人

## ⚠️ 重要说明
**Vercel 不适合运行传统的 Discord Bot！** 
- Vercel 是为无服务器函数和静态网站设计的
- Discord Bot 需要24/7持续运行的WebSocket连接
- Vercel 函数有10秒执行时间限制（Pro版30秒）
- 本指南将帮助你部署为 Webhook 模式（有限功能）

## 📋 前置要求
1. GitHub 账号
2. Vercel 账号（免费）
3. Discord 开发者账号
4. OpenAI API Key

---

## 📝 Step 1: 准备项目

### 1.1 创建 GitHub 仓库
1. 登录 GitHub (https://github.com)
2. 点击 "New repository"
3. 输入仓库名称：`discord-translator-bot`
4. 设置为 Private（私有）
5. 点击 "Create repository"

### 1.2 上传代码到 GitHub
```bash
# 在你的项目目录中
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/你的用户名/discord-translator-bot.git
git push -u origin main
```

---

## 📦 Step 2: 配置 Discord Application

### 2.1 创建 Discord Application
1. 访问 https://discord.com/developers/applications
2. 点击 "New Application"
3. 输入应用名称
4. 点击 "Create"

### 2.2 配置 Bot
1. 左侧菜单选择 "Bot"
2. 点击 "Add Bot"
3. 复制 Token（这就是你的 DISCORD_TOKEN）
4. **重要**：保存好这个 Token，不要分享给任何人！

### 2.3 配置 Interactions Endpoint (Webhook)
1. 左侧菜单选择 "General Information"
2. 记下 "Application ID"
3. 稍后我们会在这里配置 Vercel URL

---

## 🔧 Step 3: 配置 Vercel

### 3.1 注册/登录 Vercel
1. 访问 https://vercel.com
2. 使用 GitHub 账号登录
3. 授权 Vercel 访问你的 GitHub

### 3.2 导入项目
1. 在 Vercel Dashboard，点击 "Add New..."
2. 选择 "Project"
3. 点击 "Import Git Repository"
4. 选择你的 `discord-translator-bot` 仓库
5. 点击 "Import"

### 3.3 配置环境变量
在项目配置页面：

1. 找到 "Environment Variables" 部分
2. 添加以下变量：

| 变量名 | 值 | 说明 |
|--------|-----|------|
| DISCORD_TOKEN | 你的Discord Bot Token | 从Discord Developer Portal获取 |
| OPENAI_KEY | 你的OpenAI API Key | 从OpenAI获取 |

3. 点击每个变量旁边的 "Add"

### 3.4 部署项目
1. 确认所有配置正确
2. 点击 "Deploy"
3. 等待部署完成（约1-2分钟）
4. 部署成功后，你会得到一个URL，例如：`https://your-project.vercel.app`

---

## 🔗 Step 4: 连接 Discord 和 Vercel

### 4.1 设置 Interactions Endpoint
1. 回到 Discord Developer Portal
2. 在你的应用中，选择 "General Information"
3. 找到 "Interactions Endpoint URL"
4. 输入：`https://your-project.vercel.app/api/webhook`
5. 点击 "Save Changes"
6. Discord 会验证你的端点（应该显示绿色勾号）

### 4.2 添加 Bot 到服务器
1. 在 Discord Developer Portal，选择 "OAuth2" > "URL Generator"
2. 在 Scopes 中选择：
   - `bot`
   - `applications.commands`
3. 在 Bot Permissions 中选择：
   - Send Messages
   - Read Message History
   - Add Reactions
   - Use Slash Commands
4. 复制生成的 URL
5. 在浏览器中打开这个 URL
6. 选择你的服务器
7. 点击 "Authorize"

---

## 🎮 Step 5: 创建 Slash Commands

### 5.1 注册命令
创建一个新文件 `register_commands.py`：

```python
import requests
import os
from dotenv import load_dotenv

load_dotenv()

APPLICATION_ID = "你的Application ID"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = "你的服务器ID"  # 可选，用于测试

url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/guilds/{GUILD_ID}/commands"

headers = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type": "application/json"
}

# 翻译命令
translate_command = {
    "name": "translate",
    "description": "翻译文本",
    "options": [
        {
            "name": "text",
            "description": "要翻译的文本",
            "type": 3,  # STRING type
            "required": True
        },
        {
            "name": "to",
            "description": "目标语言 (en/zh)",
            "type": 3,
            "required": False,
            "choices": [
                {"name": "英文", "value": "en"},
                {"name": "中文", "value": "zh"}
            ]
        }
    ]
}

response = requests.post(url, headers=headers, json=translate_command)
print(f"Status Code: {response.status_code}")
print(f"Response: {response.json()}")
```

运行这个脚本注册命令：
```bash
python register_commands.py
```

---

## 📊 Step 6: 监控和调试

### 6.1 查看 Vercel 日志
1. 在 Vercel Dashboard 中打开你的项目
2. 点击 "Functions" 标签
3. 点击 "View Logs"
4. 这里可以看到所有请求和错误日志

### 6.2 测试 Webhook
```bash
# 测试健康检查
curl https://your-project.vercel.app/api/webhook

# 应该返回: "Discord Bot Webhook is running"
```

---

## 🚨 常见问题解决

### Q1: Discord 验证端点失败
**解决方案**：
- 确保 webhook.py 正确处理 type=1 的 ping 请求
- 检查 Vercel 部署是否成功
- 查看 Vercel 函数日志

### Q2: 命令不工作
**解决方案**：
- 确保命令已正确注册
- 检查 Bot 权限
- 查看 Vercel 日志中的错误信息

### Q3: 环境变量未生效
**解决方案**：
1. 在 Vercel Dashboard > Settings > Environment Variables
2. 确保变量名称完全匹配
3. 重新部署项目

---

## 🎯 推荐的替代方案

由于 Vercel 的限制，建议使用以下平台部署 Discord Bot：

### 1. **Railway** (推荐) 
- 支持 24/7 运行
- 免费套餐每月 $5 额度
- 一键部署
- 网址：https://railway.app

### 2. **Render**
- 免费套餐可用
- 支持持续运行
- 自动部署
- 网址：https://render.com

### 3. **Heroku** (付费)
- 稳定可靠
- 需要付费（约 $7/月）
- 网址：https://heroku.com

### 4. **VPS 服务器**
- 完全控制
- 可选择：DigitalOcean, Linode, Vultr
- 价格：$5-10/月

### 5. **Replit**
- 在线 IDE
- 支持 Always On（付费）
- 适合测试和开发
- 网址：https://replit.com

---

## 📚 Railway 部署教程（推荐）

### Step 1: 准备 railway.json
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python bot.py",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

### Step 2: 创建 Procfile
```
worker: python bot.py
```

### Step 3: 部署到 Railway
1. 访问 https://railway.app
2. 使用 GitHub 登录
3. 点击 "New Project"
4. 选择 "Deploy from GitHub repo"
5. 选择你的仓库
6. 添加环境变量
7. 点击 "Deploy"

---

## 💡 最终建议

1. **开发测试**：使用本地环境或 Replit
2. **生产部署**：使用 Railway 或 Render
3. **高级需求**：使用 VPS 服务器

Vercel 适合：
- Webhook 模式的简单交互
- Slash Commands
- 不需要实时消息监听的功能

Vercel 不适合：
- 需要监听所有消息的 Bot
- 需要保持状态的功能
- 实时翻译功能

---

## 📞 需要帮助？

1. Discord.py 文档：https://discordpy.readthedocs.io/
2. Vercel 文档：https://vercel.com/docs
3. Railway 文档：https://docs.railway.app/
4. Discord Developer Portal：https://discord.com/developers/docs

祝你部署成功！🎉
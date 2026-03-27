import os
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# 設定日誌 (繁體中文)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('discord_bot')

class MyBot(commands.Bot):
    def __init__(self):
        # 設定必要的 Intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """
        初始化連結：自動載入 dctool 目錄下的所有 Cog 檔案
        """
        logger.info("正在載入擴充套件 (Cogs)...")
        # 遍歷 dctool 資料夾
        for filename in os.listdir("./dctool"):
            if filename.endswith("_cog.py"):
                extension = f"dctool.{filename[:-3]}"
                try:
                    await self.load_extension(extension)
                    logger.info(f"✅ 成功載入擴充套件: {extension}")
                except Exception as e:
                    logger.error(f"❌ 載入 {extension} 失敗: {e}")

    async def on_ready(self):
        logger.info(f"🚀 機器人已上線: {self.user.name} (ID: {self.user.id})")
        # 同步 Slash 指令
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ 已同步 {len(synced)} 個全域 Slash 指令")
        except Exception as e:
            logger.error(f"❌ 同步指令失敗: {e}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("錯誤：找不到 DISCORD_TOKEN，請檢查 .env 檔案")
    else:
        bot = MyBot()
        bot.run(DISCORD_TOKEN)

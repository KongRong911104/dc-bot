# Ubuntu 部署指南 (Discord Bot)

本指南將引導您如何在 Ubuntu 系統上透過 systemd 部署此 Discord 機器人。

## 1. 建立虛擬環境 (Virtual Environment)

在專案目錄下執行以下指令：

```bash
# 安裝 Python 虛擬環境套件 (若尚未安裝)
sudo apt update
sudo apt install python3-venv python3-pip -y

# 建立虛擬環境
python3 -m venv venv

# 啟動虛擬環境並安裝依賴項
source venv/bin/activate
pip install -r requirements.txt
```

## 2. 設定環境變數

請複製 `.env.example` 並重新命名為 `.env`，然後填入您的金鑰：


```bash
cp .env.example .env
nano .env
```
WEATHER_API 是中央氣象局的/v1/rest/datastore/F-D0047-093
必填 Authorization 、 locationId 、 LocationName 、ElementName的天氣預報綜合描述、12小時降雨機率、紫外線指數
DISCORD_CHANNEL_ID 是 預報的頻道
## 3. 設定 Systemd 服務

建立一個新的服務檔案 `/etc/systemd/system/discord-bot.service`：

```bash
sudo nano /etc/systemd/system/discord-bot.service
```

將以下內容貼入該檔案（請將 `/home/user/dc-bot` 替換為您的實際路徑，`user` 替換為您的使用者名稱）：

```ini
[Unit]
Description=Discord Gemini Bot Service
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user/dc-bot
ExecStart=/home/user/dc-bot/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=discord-bot

[Install]
WantedBy=multi-user.target
```

## 4. 啟動與管理服務

執行以下指令來啟動並啟用服

```bash
# 重新載入 systemd 配置
sudo systemctl daemon-reload

# 啟動服務
sudo systemctl start discord-bot

# 設定為開機自動啟動
sudo systemctl enable discord-bot

# 檢查服務狀態
sudo systemctl status discord-bot
```

## 5. 查看日誌

若需要查看機器人的執行日誌：

```bash
journalctl -u discord-bot -f
```

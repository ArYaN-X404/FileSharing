# 🚀 Standalone File-Sharing Bot Deployment Guide

This bot can be hosted **completely independently** from any other component (no leech bot required).

---

## 🛠️ Quick Start (Local / VPS)

### Option 1: Using Docker (Recommended)

1. Clone or copy the `File-Sharing-Bot` directory to your server.
2. Copy `.env.example` to `.env` and fill in your values:
   ```bash
   cp .env.example .env
   nano .env
   ```
3. Run with Docker Compose:
   ```bash
   docker compose up -d --build
   ```
4. View live logs:
   ```bash
   docker compose logs -f
   ```

---

### Option 2: Running Directly with Python

1. Install Python 3.10+
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the bot:
   ```bash
   python main.py
   ```

---

## 🌐 Deploying to Cloud (Render / Railway / Koyeb)

1. Connect your GitHub repository pointing to this `File-Sharing-Bot` folder.
2. Select **Docker** as the environment (or Python 3).
3. Set the Environment Variables from `.env` in the platform dashboard:
   - `TG_BOT_TOKEN`
   - `APP_ID`
   - `API_HASH`
   - `CHANNEL_ID`
   - `HMAC_TOKEN_SECRET`
   - `PORT=8085`
4. The service will build and start automatically.

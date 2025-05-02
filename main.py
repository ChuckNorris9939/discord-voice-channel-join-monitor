import os
import threading
from flask import Flask
import discord
from discord import Intents, Thread
from discord.ext import commands, tasks
# from keep_up import keep_awake

# --------- Flask-Server für Health Checks ---------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot ist online!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --------- Discord-Bot Setup ---------
intents = Intents.default()
intents.guilds = True
intents.message_content = True
intents.guild_messages = True  # Für Thread-Events

bot = commands.Bot(command_prefix="!", intents=intents)

# Konfiguration
message_delete_count = 15
TECHSUPPORT_CHANNEL_ID = 1139952610883928134  # Forum-Channel für Threads
CLOSED_TAG_NAME = "🔒 CLOSED"
LOG_CHANNEL_ID = 1266773678306230374  # Channel für Logs
HIDDEN_CHANNELS = [1255930025463644232, 1233872680680296499]
USERS = []

# Hilfsfunktion zum Senden von Nachrichten
def send_msg(channel_id, msg):
    async def _send():
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        await channel.send(msg)
    return _send

@bot.event
async def on_ready():
    print(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    threading.Thread(target=run_flask, daemon=True).start()
    print("Flask-Server läuft für Health Checks.")
    # keep_awake() / jetzt hier drinnen
    # Führe initiale Nachrichtenlöschung synchron aus
    await msg_purge()
    # Starte den wiederkehrenden Purge-Task
    # msg_purge.start()
    # user_list wird später nach Initialisierung gestartet

    await send_msg(LOG_CHANNEL_ID, "✅ Bot started")()
    # Initialisiere Benutzerliste basierend auf aktuellen Voice-Kanälen
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if vc.id not in HIDDEN_CHANNELS:
                for member in vc.members:
                    if member.name not in USERS:
                        USERS.append(member.name)

    # Schöne Ausgabe der User-Liste
    formatted_users = [f"***{u}***" for u in USERS]
    user_list_msg = f"👥 {len(USERS)} Nutzer online: {', '.join(formatted_users) if USERS else 'keine'}"
    await send_msg(LOG_CHANNEL_ID, user_list_msg)()
    print(f"Initial Users online: {USERS}")
    # Starte user_list nach Initialisierung
    # user_list.start()


    # Initiale Verarbeitung: bereits existierende Threads
    channel = bot.get_channel(TECHSUPPORT_CHANNEL_ID) or await bot.fetch_channel(TECHSUPPORT_CHANNEL_ID)
    threads = []
    if hasattr(channel, 'threads'):
        threads.extend(channel.threads)
    try:
        archived_threads = []
        async for t in channel.archived_threads(limit=None):
            archived_threads.append(t)
        threads.extend(archived_threads)
    except Exception as e:
        print(f"Fehler beim Abrufen archivierter Threads: {e}")

    for thread in threads:
        tags = {tag.name.lower() for tag in thread.applied_tags}
        if CLOSED_TAG_NAME.lower() in tags and not thread.archived:
            try:
                await thread.send(f"🔒 Dieser Support-Thread wurde geschlossen und gesperrt (Tag: {CLOSED_TAG_NAME}).")
                await thread.edit(locked=True)
                await thread.edit(archived=True)
                print(f"Vorhandener Thread '{thread.name}' wurde gesperrt und archiviert.")
            except Exception as e:
                print(f"Fehler bei initialer Verarbeitung von Thread '{thread.name}': {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.content.startswith('$hello'):
        await message.channel.send('Hello!')
    if message.content.startswith('$delete'):
        print("starte msg_purge, manual 50")
        channel = bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            await channel.purge(limit=50)
        print("msg_purge abgeschlossen")

@bot.event
async def on_voice_state_update(member, before, after):
    # Join-Event
    if (before.channel is None or before.channel.id in HIDDEN_CHANNELS) and after.channel is not None:
        if after.channel.id not in HIDDEN_CHANNELS:
            channel_name = after.channel.name
            user_name = member.name
            await send_msg(LOG_CHANNEL_ID, f"➕ ***{user_name}*** joined ***{channel_name}***")()
            USERS.append(member.name)
            # Schöne Ausgabe der aktuellen User-Liste
            formatted_users = [f"***{u}***" for u in USERS]
            user_list_msg = f"👥 {len(USERS)} Nutzer online: {', '.join(formatted_users)}"
            await send_msg(LOG_CHANNEL_ID, user_list_msg)()
            print(f"User {member} joined voice channel {after.channel}")
    # Leave-Event
    if (before.channel is not None and after.channel is None) or (after.channel and after.channel.id in HIDDEN_CHANNELS):
        if before.channel and before.channel.id not in HIDDEN_CHANNELS:
            channel_name = before.channel.name
            user_name = member.name
            await send_msg(LOG_CHANNEL_ID, f"➖ ***{user_name}*** left ***{channel_name}***")()
            try:
                USERS.remove(member.name)
            except ValueError:
                pass
            # Schöne Ausgabe der aktuellen User-Liste
            formatted_users = [f"***{u}***" for u in USERS]
            user_list_msg = f"👥 {len(USERS)} Nutzer online: {', '.join(formatted_users) if USERS else 'keine'}"
            await send_msg(LOG_CHANNEL_ID, user_list_msg)()
            print(f"User {member} left voice channel {before.channel}")

@tasks.loop(hours=24)
async def msg_purge():
    print("starte msg_purge, last {message_delete_count} messages")
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.purge(limit=message_delete_count)
    print("msg_purge abgeschlossen")

# @tasks.loop(hours=24)
# async def user_list():
#     # Schöne Ausgabe der aktuellen User-Liste
#     formatted_users = [f"***{u}***" for u in USERS]
#     user_list_msg = f"👥 {len(USERS)} Nutzer online: {', '.join(formatted_users) if USERS else 'keine'}"
#     await send_msg(LOG_CHANNEL_ID, user_list_msg)()

@bot.event
async def on_thread_update(before: Thread, after: Thread):
    parent = after.parent
    if parent and parent.id == TECHSUPPORT_CHANNEL_ID:
        before_tags = {tag.name.lower() for tag in before.applied_tags}
        after_tags = {tag.name.lower() for tag in after.applied_tags}
        if CLOSED_TAG_NAME.lower() in after_tags and CLOSED_TAG_NAME.lower() not in before_tags:
            try:
                await after.send(f"🔒 Dieser Support-Thread wurde geschlossen und gesperrt (Tag: {CLOSED_TAG_NAME}).")
                await after.edit(locked=True)
                await after.edit(archived=True)
                print(f"Thread '{after.name}' wurde gesperrt und archiviert.")
            except Exception as e:
                print(f"Fehler beim Sperren/Archivieren des Threads: {e}")

if __name__ == "__main__":
    TOKEN = os.environ.get("TOKEN")
    if not TOKEN:
        print("Fehler: Umgebungsvariable 'TOKEN' ist nicht gesetzt.")
    else:
        bot.run(TOKEN)

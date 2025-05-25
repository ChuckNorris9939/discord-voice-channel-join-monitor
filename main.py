# v1.8 (Umstellung von print auf logger)
import os
import threading
import datetime
import random
import sqlite3 # Added import for sqlite3
from flask import Flask
import discord
from discord import Intents, Thread, File, Embed
from discord.ext import commands, tasks
from typing import Dict, List, Optional
import signal
import asyncio

# --------- Logging ---------
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("discord_bot") # Spezifischer Name für den Bot-Logger

# --------- Flask-Server für Health Checks ---------
from waitress import serve
app = Flask(__name__)

@app.route("/")
def home():
    # Diese print-Anweisung kann bleiben oder zu logger.debug/info für Flask-spezifische Logs werden
    # logger.info("Flask: Health-Check-Endpunkt / wurde aufgerufen.")
    return "Bot ist online!"

def run_flask():
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8080))
    # Diese print-Anweisung ist eine einmalige Startmeldung für Waitress und kann so bleiben.
    print(f"Starte Waitress WSGI-Server auf {host}:{port}")
    serve(app, host=host, port=port, threads=4)

# --------- Discord-Bot Setup ---------
intents = Intents.default()
intents.guilds = True
intents.message_content = True
intents.guild_messages = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!!", intents=intents)

# Konfiguration
DISCORD_SERVER_ID = 374159356717039616
TECHSUPPORT_CHANNEL_ID = 1139952610883928134
CLOSED_TAG_NAME = "🔒 CLOSED"
LOG_CHANNEL_ID = 1266773678306230374
BOT_AUDIT_ID = 1373288909542264852
HIDDEN_CHANNELS = [1255930025463644232, 1233872680680296499, 374159356717039620]
USERS: List[str] = []
IMAGES_FOLDER = "images"

shutdown_initiated = False

async def send_log_message(msg: str, embed: Optional[Embed] = None, target_channel_ids: Optional[List[int]] = None):
    if target_channel_ids is None:
        if BOT_AUDIT_ID:
            target_channel_ids = [BOT_AUDIT_ID]
        else:
            logger.error(f"send_log_message: BOT_AUDIT_ID ist nicht konfiguriert. Nachricht kann nicht gesendet werden: {msg}")
            return

    if not target_channel_ids:
        logger.warning(f"send_log_message: Keine Zielkanäle für Nachricht: {msg}")
        return

    for channel_id in target_channel_ids:
        if channel_id is None:
            logger.warning(f"send_log_message: Ungültige Kanal-ID (None) beim Senden der Log-Nachricht übersprungen: {msg}")
            continue
        try:
            if not bot.is_ready() and not (bot.loop and bot.loop.is_running()):
                logger.warning(f"send_log_message: Log-Nachricht an {channel_id} kann nicht gesendet werden, Bot nicht bereit/Loop nicht vorhanden: {msg}")
                continue

            log_channel_obj = bot.get_channel(channel_id)
            if not log_channel_obj:
                if bot.user and bot.loop and bot.loop.is_running() and not bot.is_closed():
                    try:
                        log_channel_obj = await bot.fetch_channel(channel_id)
                    except discord.NotFound:
                        logger.warning(f"send_log_message: Log-Kanal {channel_id} nicht gefunden (fetch_channel). Nachricht: {msg}")
                    except discord.Forbidden:
                        logger.warning(f"send_log_message: Log-Kanal {channel_id} nicht zugänglich (fetch_channel, Forbidden). Nachricht: {msg}")
                    except Exception as e_fetch:
                        logger.error(f"send_log_message: Fehler beim Holen des Log-Kanals {channel_id}: {e_fetch}. Nachricht: {msg}", exc_info=True)
                else:
                    logger.warning(f"send_log_message: Log-Kanal {channel_id} nicht gefunden (get_channel) und Bot nicht bereit für fetch. Nachricht: {msg}")

            if log_channel_obj:
                await log_channel_obj.send(msg, embed=embed)
            else:
                logger.error(f"send_log_message: Log-Kanal {channel_id} konnte endgültig nicht gefunden werden. Nachricht: {msg}")
        except Exception as e:
            logger.error(f"send_log_message: Fehler beim Senden der Log-Nachricht an Kanal {channel_id}: {e}. Ursprüngliche Nachricht: {msg}", exc_info=True)


async def get_forum_tag_by_name(forum_channel: discord.ForumChannel, tag_name: str) -> discord.ForumTag | None:
    if not forum_channel:
        return None
    for tag in forum_channel.available_tags:
        if tag.name.lower() == tag_name.lower():
            return tag
    return None

async def close_support_thread(thread: Thread, trigger_source: str, set_tag: bool = True):
    try:
        actions_performed = []
        log_actions = []

        if not thread.locked:
            await thread.edit(locked=True)
            actions_performed.append("gesperrt")
            log_actions.append("gesperrt")

        closed_tag_object = None
        if set_tag and isinstance(thread.parent, discord.ForumChannel):
            closed_tag_object = await get_forum_tag_by_name(thread.parent, CLOSED_TAG_NAME)
            if closed_tag_object:
                current_tags = thread.applied_tags
                if closed_tag_object not in current_tags:
                    new_tags = [t for t in current_tags if t.name.lower() != CLOSED_TAG_NAME.lower()]
                    new_tags.append(closed_tag_object)
                    try:
                        await thread.edit(applied_tags=new_tags[:5]) 
                        actions_performed.append(f"Tag '{CLOSED_TAG_NAME}' gesetzt")
                        log_actions.append("Tag gesetzt")
                    except discord.HTTPException as e:
                        logger.error(f"Fehler beim Setzen des Tags für Thread '{thread.name}': {e}", exc_info=True)
                        await send_log_message(f"⚠️ Fehler beim Setzen des Tags '{CLOSED_TAG_NAME}' für Thread '{thread.name}': {e.text}", target_channel_ids=[BOT_AUDIT_ID])
            else:
                logger.warning(f"Tag '{CLOSED_TAG_NAME}' wurde im Forum '{thread.parent.name}' nicht gefunden.")
                await send_log_message(f"⚠️ Warnung: Tag '{CLOSED_TAG_NAME}' im Forum '{thread.parent.name}' nicht gefunden für Thread '{thread.name}'.", target_channel_ids=[BOT_AUDIT_ID])

        if actions_performed or not thread.archived:
            message_parts = ["🔒 Dieser Support-Thread wurde"]
            if trigger_source:
                message_parts.append(f"durch {trigger_source}")
            if actions_performed:
                message_parts.append(f"{' und '.join(actions_performed)}")
            else:
                message_parts.append("bearbeitet")
            message_parts.append("und wird nun archiviert.")
            await thread.send(" ".join(message_parts))

        if not thread.archived:
            await thread.edit(archived=True)
            log_actions.append("archiviert")

        if log_actions:
            action_str = " und ".join(log_actions)
            logger.info(f"Thread '{thread.name}' wurde durch {trigger_source} {action_str}.")
            await send_log_message(f"🧵 Thread '{thread.name}' (ID: {thread.id}) durch '{trigger_source}' {action_str}.", target_channel_ids=[BOT_AUDIT_ID])

    except discord.Forbidden:
        err_msg = f"Fehler: Keine Berechtigung, den Thread '{thread.name}' zu bearbeiten (sperren/Tag/archivieren)."
        logger.error(err_msg)
        await send_log_message(f"⚠️ {err_msg}", target_channel_ids=[BOT_AUDIT_ID])
        try:
            await thread.send(f"Fehler: Ich habe nicht die nötigen Berechtigungen, um diesen Thread zu sperren, den Tag zu setzen oder zu archivieren. Bitte überprüfe meine Rollen und Berechtigungen im Kanal '{thread.parent.name}'.")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Generischer Fehler beim Schließen des Threads '{thread.name}': {e}", exc_info=True)
        await send_log_message(f"⚠️ Fehler beim Schließen des Threads '{thread.name}' (ID: {thread.id}): {e}", target_channel_ids=[BOT_AUDIT_ID])

@bot.event
async def on_ready():
    logger.info(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    if not os.path.exists(IMAGES_FOLDER):
        os.makedirs(IMAGES_FOLDER)
        logger.info(f"Ordner '{IMAGES_FOLDER}' wurde erstellt. Bitte füge Bilder hinzu.")
        await send_log_message(f"⚠️ Ordner '{IMAGES_FOLDER}' wurde erstellt. Bitte Bilder für den `delete`-Befehl hinzufügen.", target_channel_ids=[BOT_AUDIT_ID])

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask-Server-Thread gestartet für Health Checks.")

    log_channel_names_to_check = {}
    if LOG_CHANNEL_ID: log_channel_names_to_check[LOG_CHANNEL_ID] = "Primär-Log"
    if BOT_AUDIT_ID: log_channel_names_to_check[BOT_AUDIT_ID] = "Audit-Log"

    for cid, cname in log_channel_names_to_check.items():
        try:
            ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
            if not ch:
                logger.warning(f"WICHTIG: {cname}-Kanal (ID: {cid}) konnte beim Start nicht gefunden werden.")
        except Exception as e_ch_check:
            logger.error(f"WICHTIG: Fehler beim Überprüfen des {cname}-Kanals (ID: {cid}): {e_ch_check}", exc_info=True)

    try:
        guild_obj = discord.Object(id=DISCORD_SERVER_ID)
        synced_commands = await bot.tree.sync(guild=guild_obj)
        num_synced = len(synced_commands) if synced_commands else 0
        command_names = [cmd.name for cmd in synced_commands] if synced_commands else []
        
        logger.info(f"{num_synced} Befehle für Guild {DISCORD_SERVER_ID} synchronisiert: {command_names}")

        await send_log_message(
            "✅ Bot gestartet.",
            target_channel_ids=[LOG_CHANNEL_ID, BOT_AUDIT_ID]
        )
        sync_info_msg = f"{num_synced} Befehle für Guild {DISCORD_SERVER_ID} synchronisiert: {command_names}"
        await send_log_message(
            f"ℹ️ {sync_info_msg}",
            target_channel_ids=[BOT_AUDIT_ID]
        )

    except Exception as e:
        logger.error(f"Fehler beim Synchronisieren der Befehle: {e}", exc_info=True)
        await send_log_message(
            f"⚠️ Bot gestartet, aber Fehler beim Synchronisieren der Befehle: {e}",
            target_channel_ids=[LOG_CHANNEL_ID, BOT_AUDIT_ID]
        )

    if not msg_purge_task.is_running():
        msg_purge_task.start()
        logger.info("msg_purge_task gestartet.")

    init_user_log_db() # Initialize user log database

    USERS.clear()
    for guild in bot.guilds:
        if guild.id == DISCORD_SERVER_ID:
            for vc in guild.voice_channels:
                if vc.id not in HIDDEN_CHANNELS:
                    for member in vc.members:
                        if not member.bot and member.name not in USERS:
                            USERS.append(member.name)
    USERS.sort()

    formatted_users = [f"***{u}***" for u in USERS]
    user_list_msg = f"👥 {len(USERS)} Nutzer online (beim Start): {', '.join(formatted_users) if USERS else 'keine'}"
    await send_log_message(user_list_msg, target_channel_ids=[LOG_CHANNEL_ID])

    try:
        tech_support_forum = bot.get_channel(TECHSUPPORT_CHANNEL_ID) or await bot.fetch_channel(TECHSUPPORT_CHANNEL_ID)
        if isinstance(tech_support_forum, discord.ForumChannel):
            closed_tag_obj_on_ready = await get_forum_tag_by_name(tech_support_forum, CLOSED_TAG_NAME)
            if not closed_tag_obj_on_ready:
                await send_log_message(f"⚠️ WICHTIG: Der Tag '{CLOSED_TAG_NAME}' konnte im Forum '{tech_support_forum.name}' (ID: {tech_support_forum.id}) nicht gefunden werden. Die automatische Schließung per Tag funktioniert nicht korrekt.", target_channel_ids=[BOT_AUDIT_ID])
        elif tech_support_forum:
            await send_log_message(f"⚠️ Tech-Support-Kanal {TECHSUPPORT_CHANNEL_ID} ('{tech_support_forum.name}') ist kein Forum-Kanal.", target_channel_ids=[BOT_AUDIT_ID])
        else:
            await send_log_message(f"⚠️ Tech-Support-Kanal {TECHSUPPORT_CHANNEL_ID} konnte nicht gefunden werden.", target_channel_ids=[BOT_AUDIT_ID])
    except Exception as e:
        logger.error(f"Fehler bei der initialen Prüfung des Tech-Support-Forums (on_ready): {e}", exc_info=True)
        await send_log_message(f"⚠️ Fehler bei der initialen Prüfung des Tech-Support-Forums (on_ready): {e}", target_channel_ids=[BOT_AUDIT_ID])

@bot.hybrid_command(name="close", description="Schließt den aktuellen Support-Thread.")
async def close(ctx: commands.Context):
    if not (isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id == TECHSUPPORT_CHANNEL_ID):
        await ctx.send("Dieser Befehl kann nur in einem Support-Thread des Tech-Support-Forums verwendet werden.", ephemeral=True)
        return
    thread = ctx.channel
    forum_channel = thread.parent
    if not isinstance(forum_channel, discord.ForumChannel):
        await ctx.send("Fehler: Der übergeordnete Kanal ist kein Forum-Kanal. Kann den Tag nicht verwalten.", ephemeral=True)
        return
    closed_tag_object = await get_forum_tag_by_name(forum_channel, CLOSED_TAG_NAME)
    if not closed_tag_object:
        await ctx.send(f"Warnung: Der Tag '{CLOSED_TAG_NAME}' wurde im Forum nicht gefunden. Der Thread wird gesperrt und archiviert, aber der Tag kann nicht gesetzt werden.", ephemeral=True)
        await send_log_message(f"⚠️ Warnung bei Befehl `close` in Thread '{thread.name}': Tag '{CLOSED_TAG_NAME}' im Forum nicht gefunden.", target_channel_ids=[BOT_AUDIT_ID])
    
    has_closed_tag = any(tag.id == closed_tag_object.id for tag in thread.applied_tags) if closed_tag_object else False
    
    already_fully_closed = thread.locked and (has_closed_tag if closed_tag_object else True) and thread.archived
    if already_fully_closed:
        await ctx.send("Dieser Thread ist bereits als geschlossen markiert (gesperrt, getaggt und archiviert).", ephemeral=True)
        return

    if thread.locked and (has_closed_tag if closed_tag_object else True) and not thread.archived:
        await ctx.send("Dieser Thread ist bereits gesperrt und getaggt, wird nun zusätzlich archiviert.", ephemeral=True)
        try:
            await thread.edit(archived=True)
            await send_log_message(f"ℹ️ Thread '{thread.name}' war gesperrt/getagged, aber nicht archiviert. Jetzt archiviert nach `close`-Befehl von {ctx.author.mention}.", target_channel_ids=[BOT_AUDIT_ID])
        except Exception as e:
            await send_log_message(f"⚠️ Fehler beim erneuten Archivieren von Thread '{thread.name}': {e}", target_channel_ids=[BOT_AUDIT_ID])
        return

    trigger_name = ctx.author.mention if ctx.author else "einem unbekannten Benutzer"
    trigger = f"Befehl `/{ctx.invoked_with}` von {trigger_name}" if ctx.interaction else f"Befehl `{bot.command_prefix}{ctx.invoked_with}` von {trigger_name}"
    
    await ctx.send("Der Schließvorgang für den Thread wird eingeleitet...", ephemeral=True)
    await close_support_thread(thread, trigger_source=trigger, set_tag=True)


@bot.hybrid_command(name="delete", description="Sendet eine Info-Nachricht und löscht dann Nachrichten im aktuellen Kanal.")
@commands.has_permissions(manage_messages=True)
@commands.guild_only()
async def delete(ctx: commands.Context, anzahl: int):
    if not (0 < anzahl <= 50):
        await ctx.send("Bitte gib eine Zahl zwischen 1 und 50 für die zu löschenden Nachrichten an.", ephemeral=True)
        return

    target_channel = ctx.channel
    if not isinstance(target_channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
        await ctx.send("Dieser Befehl kann nur in Textkanälen, Voice-Kanal-Chats oder Threads verwendet werden.", ephemeral=True)
        return

    image_file_to_send = None
    image_name_for_embed = None
    try:
        available_images = [f for f in os.listdir(IMAGES_FOLDER) if os.path.isfile(os.path.join(IMAGES_FOLDER, f))]
        if not available_images:
            await ctx.send(f"Keine Bilder im Ordner '{IMAGES_FOLDER}' gefunden. Bitte füge welche hinzu.", ephemeral=True)
            await send_log_message(f"⚠️ Versuchter `delete`-Befehl, aber keine Bilder in '{IMAGES_FOLDER}' durch {ctx.author.mention} in #{target_channel.name}.", target_channel_ids=[BOT_AUDIT_ID])
            return
        chosen_image_name = random.choice(available_images)
        image_path = os.path.join(IMAGES_FOLDER, chosen_image_name)
        image_file_to_send = discord.File(image_path, filename=chosen_image_name)
        image_name_for_embed = chosen_image_name
    except FileNotFoundError:
        await ctx.send(f"Fehler: Der Bilderordner '{IMAGES_FOLDER}' wurde nicht gefunden.", ephemeral=True)
        await send_log_message(f"⚠️ Bilderordner '{IMAGES_FOLDER}' nicht gefunden bei `delete`-Befehl durch {ctx.author.mention} in #{target_channel.name}.", target_channel_ids=[BOT_AUDIT_ID])
        return
    except Exception as e:
        await ctx.send("Ein Fehler ist bei der Bildauswahl aufgetreten.", ephemeral=True)
        await send_log_message(f"⚠️ Fehler bei Bildauswahl für `delete` durch {ctx.author.mention} in #{target_channel.name}: {e}", target_channel_ids=[BOT_AUDIT_ID])
        return

    embed = Embed(description="Delet this", color=discord.Color.blue())
    if image_name_for_embed:
         embed.set_image(url=f"attachment://{image_name_for_embed}")

    info_message = None
    try:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"Info-Nachricht wird gesendet und {anzahl} vorherige Nachrichten werden gelöscht...", ephemeral=True, delete_after=10)
        else: 
            await ctx.send(f"Info-Nachricht wird gesendet und {anzahl} vorherige Nachrichten werden gelöscht...", delete_after=10)
    except discord.HTTPException as e:
         logger.warning(f"Konnte die temporäre Bestätigungsnachricht für delete nicht senden: {e}")


    try:
        info_message = await target_channel.send(file=image_file_to_send, embed=embed)
    except discord.Forbidden:
        err_msg_user = "Ich habe keine Berechtigung, Nachrichten oder Bilder in diesem Kanal zu senden."
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await ctx.send(err_msg_user, delete_after=15)
        await send_log_message(f"⚠️ Keine Sende-Berechtigung für `delete`-Info in #{target_channel.name} (Versuch von {ctx.author.mention}).", target_channel_ids=[BOT_AUDIT_ID])
        return
    except Exception as e:
        err_msg_user = f"Ein Fehler ist beim Senden der Info-Nachricht aufgetreten: {e}"
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await ctx.send(err_msg_user, delete_after=15)
        await send_log_message(f"⚠️ Fehler beim Senden der `delete`-Info in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}", target_channel_ids=[BOT_AUDIT_ID])
        return

    deleted_messages_count = 0
    try:
        def check(m):
            is_command_message = False
            if ctx.message: 
                 is_command_message = (m.id == ctx.message.id)
            elif ctx.interaction and ctx.interaction.message: 
                 is_command_message = (m.id == ctx.interaction.message.id)
            
            is_info_message = (info_message and m.id == info_message.id)
            return not is_command_message and not is_info_message

        before_message = info_message 
        if ctx.message: 
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                logger.warning(f"Konnte die Befehlsnachricht von {ctx.author} nicht löschen.")


        deleted_messages = await target_channel.purge(limit=anzahl, check=check, before=before_message)
        deleted_messages_count = len(deleted_messages)
        
        log_msg_text = f"🗑️ {deleted_messages_count} Nachrichten in Kanal #{target_channel.name} (ID: {target_channel.id}) durch {ctx.author.mention} gelöscht (nach Info-Post)."
        await send_log_message(log_msg_text, target_channel_ids=[BOT_AUDIT_ID])
        logger.info(f"{deleted_messages_count} Nachrichten in #{target_channel.name} durch {ctx.author} gelöscht.")
    except discord.Forbidden:
        err_msg_user = "Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu löschen."
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Keine Lösch-Berechtigung in #{target_channel.name} (Versuch von {ctx.author.mention}).", target_channel_ids=[BOT_AUDIT_ID])
    except discord.HTTPException as e:
        err_msg_user = f"Ein Fehler ist beim Löschen der Nachrichten aufgetreten: {e.text if e.text else e.status}"
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Fehler beim Löschen in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}", target_channel_ids=[BOT_AUDIT_ID])
    except Exception as e:
        err_msg_user = f"Ein generischer Fehler ist beim Löschen der Nachrichten aufgetreten: {e}"
        if ctx.interaction: await ctx.followup.send(err_msg_user, ephemeral=True)
        else: await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Generischer Fehler beim Löschen in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}", target_channel_ids=[BOT_AUDIT_ID])


@delete.error
async def delete_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Du hast nicht die erforderlichen Berechtigungen, um diesen Befehl auszuführen.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Dieser Befehl kann nicht in privaten Nachrichten verwendet werden.", ephemeral=True)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Der Parameter `{error.param.name}` fehlt. Bitte gib die Anzahl der zu löschenden Nachrichten an (1-50).", ephemeral=True)
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.HTTPException) and error.original.status == 404:
        await ctx.send("Fehler: Der Kanal konnte nicht gefunden oder Nachrichten darin nicht gelöscht werden (HTTP 404).", ephemeral=True)
    else:
        await ctx.send(f"Ein Fehler ist im `delete`-Befehl aufgetreten: {error}", ephemeral=True)
    logger.error(f"Fehler im delete-Befehl von {ctx.author}: {error}", exc_info=True) # exc_info für Traceback
    await send_log_message(f"⚠️ Fehler im delete-Befehl von {ctx.author} in #{ctx.channel.name if ctx.channel else 'Unbekannter Kanal'}: {error}", target_channel_ids=[BOT_AUDIT_ID])


@bot.hybrid_command(name="users", description="Listet alle Benutzer in den sichtbaren Voice-Channels auf.")
@commands.guild_only()
async def users(ctx: commands.Context):
    output_lines = ["Aktive Benutzer in Voice-Channels:"]
    any_users_found = False
    if not ctx.guild:
        await ctx.send("Dieser Befehl muss auf einem Server ausgeführt werden.", ephemeral=True)
        return

    for vc in ctx.guild.voice_channels:
        if vc.id in HIDDEN_CHANNELS:
            continue
        current_channel_users = []
        for member in vc.members:
            if not member.bot:
                current_channel_users.append(member.display_name)
        if current_channel_users:
            any_users_found = True
            output_lines.append(f"\n🔊 **{vc.name}** ({len(current_channel_users)}):")
            output_lines.append(", ".join(sorted(current_channel_users)))
    
    if not any_users_found:
        output_lines.append("\nZurzeit sind keine Benutzer in sichtbaren Voice-Channels.")
    
    message_to_send = "\n".join(output_lines)
    
    if len(message_to_send) > 1980: 
        if ctx.interaction: await ctx.interaction.response.defer(ephemeral=False)
        else: await ctx.defer() 

        parts = []
        current_part = ""
        for line in output_lines:
            if len(current_part) + len(line) + 1 > 1980:
                parts.append(current_part)
                current_part = line
            else:
                if current_part: current_part += "\n" + line
                else: current_part = line
        if current_part: parts.append(current_part)
        
        first_sent = False
        for i, part_msg in enumerate(parts):
            if not first_sent:
                if ctx.interaction: await ctx.followup.send(part_msg, ephemeral=False)
                else: await ctx.send(part_msg)
                first_sent = True
            else:
                if ctx.interaction: await ctx.followup.send(part_msg, ephemeral=False)
                else: await ctx.channel.send(part_msg) 
    else:
        if ctx.interaction: await ctx.interaction.response.send_message(message_to_send, ephemeral=False)
        else: await ctx.send(message_to_send)

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot:
        return
    if message.guild and message.guild.id == DISCORD_SERVER_ID:
        await bot.process_commands(message)

@bot.hybrid_command(name="viewlogs", description="Zeigt die letzten 10 Benutzer-Join-Events an (nur für Admins).")
@commands.has_permissions(administrator=True)
@commands.guild_only()
async def viewlogs(ctx: commands.Context):
    conn = None
    try:
        conn = sqlite3.connect('user_log.db')
        cursor = conn.cursor()
        # Fetch last 10 records, ordering by id descending to get the latest entries
        cursor.execute("SELECT user_id, username, channel_id, channel_name, timestamp FROM user_joins ORDER BY id DESC LIMIT 10")
        records = cursor.fetchall()

        if not records:
            await ctx.send("Noch keine Join-Events in der Datenbank vorhanden.", ephemeral=True)
            return

        response_lines = ["**Letzte 10 Benutzer-Join-Events:**"]
        for record in records:
            user_id, username, channel_id, channel_name, timestamp_str = record
            # Parse ISO timestamp string back to datetime object for formatting (optional, but nice)
            try:
                dt_obj = datetime.datetime.fromisoformat(timestamp_str)
                formatted_timestamp = dt_obj.strftime('%Y-%m-%d %H:%M:%S UTC')
            except ValueError:
                formatted_timestamp = timestamp_str # Fallback if parsing fails

            response_lines.append(
                f"Benutzer: {username} (ID: {user_id}) trat Kanal bei: {channel_name} (ID: {channel_id}) um {formatted_timestamp}"
            )
        
        response_message = "\n".join(response_lines)

        # Discord message length limit is 2000 characters.
        # For 10 records, this should be fine. If it could be longer, chunking or file sending is needed.
        if len(response_message) > 1980: # Leave some buffer
            # Simple truncation for this example if too long, ideally send as file or multiple messages
            # For now, just send what fits or an error.
            # A better approach for very long messages would be to send as a discord.File
            await ctx.send("Die Log-Nachricht ist zu lang. Hier sind die ersten ~2000 Zeichen:\n" + response_message[:1950], ephemeral=True)
            # Alternative: send as file
            # with open("join_logs.txt", "w", encoding="utf-8") as f:
            # f.write(response_message)
            # await ctx.send(file=discord.File("join_logs.txt"), ephemeral=True)
            # os.remove("join_logs.txt")

        else:
            await ctx.send(response_message, ephemeral=True)

    except sqlite3.Error as e:
        logger.error(f"SQLite error when trying to view logs: {e}")
        await ctx.send(f"Ein Datenbankfehler ist aufgetreten: {e}", ephemeral=True)
    except Exception as e:
        logger.error(f"Generischer Fehler in viewlogs: {e}", exc_info=True)
        await ctx.send(f"Ein unerwarteter Fehler ist aufgetreten: {e}", ephemeral=True)
    finally:
        if conn:
            conn.close()

@viewlogs.error
async def viewlogs_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Du hast nicht die erforderlichen Berechtigungen, um diesen Befehl auszuführen.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Dieser Befehl kann nicht in privaten Nachrichten verwendet werden.", ephemeral=True)
    else:
        await ctx.send(f"Ein Fehler ist im `viewlogs`-Befehl aufgetreten: {error}", ephemeral=True)
        logger.error(f"Fehler im viewlogs-Befehl von {ctx.author}: {error}", exc_info=True)
        await send_log_message(f"⚠️ Fehler im viewlogs-Befehl von {ctx.author} in #{ctx.channel.name if ctx.channel else 'Unbekannter Kanal'}: {error}", target_channel_ids=[BOT_AUDIT_ID])

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.guild.id != DISCORD_SERVER_ID: return
    if member.bot: return

    user_name_log_format = f"***{member.name}***"
    log_needs_update_user_list = False
    target_ids_vc = [LOG_CHANNEL_ID]

    before_is_hidden = before.channel and before.channel.id in HIDDEN_CHANNELS
    after_is_hidden = after.channel and after.channel.id in HIDDEN_CHANNELS

    joined_visible_channel = after.channel and not after_is_hidden and \
                             (not before.channel or before_is_hidden)
    
    left_visible_channel = before.channel and not before_is_hidden and \
                           (not after.channel or after_is_hidden)
    
    switched_between_visible_channels = before.channel and not before_is_hidden and \
                                       after.channel and not after_is_hidden and \
                                       before.channel.id != after.channel.id
    
    if after.channel and after.channel.id not in HIDDEN_CHANNELS:
        if not before.self_stream and after.self_stream:
             await send_log_message(f"🔴 {user_name_log_format} startete einen Stream in ***{after.channel.name}***.", target_channel_ids=target_ids_vc)
        elif before.self_stream and not after.self_stream:
             await send_log_message(f"⚫ {user_name_log_format} beendete einen Stream in ***{after.channel.name}***.", target_channel_ids=target_ids_vc)
        
        if not before.self_video and after.self_video:
             await send_log_message(f"📹 {user_name_log_format} aktivierte die Kamera in ***{after.channel.name}***.", target_channel_ids=target_ids_vc)
        elif before.self_video and not after.self_video:
             await send_log_message(f"🚫📹 {user_name_log_format} deaktivierte die Kamera in ***{after.channel.name}***.", target_channel_ids=target_ids_vc)


    if joined_visible_channel:
        ch_name_log_format = f"***{after.channel.name}***"
        await send_log_message(f"➕ {user_name_log_format} hat {ch_name_log_format} betreten.", target_channel_ids=target_ids_vc)
        
        # Log user join to database
        try:
            conn = sqlite3.connect('user_log.db')
            cursor = conn.cursor()
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO user_joins (user_id, username, channel_id, channel_name, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (member.id, member.name, after.channel.id, after.channel.name, timestamp))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"SQLite error when logging user join: {e}")
        finally:
            if conn:
                conn.close()

        if member.name not in USERS:
            USERS.append(member.name)
            USERS.sort()
            log_needs_update_user_list = True
    elif left_visible_channel:
        ch_name_log_format = f"***{before.channel.name}***"
        await send_log_message(f"➖ {user_name_log_format} hat {ch_name_log_format} verlassen.", target_channel_ids=target_ids_vc)
        user_still_in_any_visible_vc_on_this_guild = False
        guild = bot.get_guild(DISCORD_SERVER_ID)
        if guild:
            member_on_guild = guild.get_member(member.id)
            if member_on_guild and member_on_guild.voice and member_on_guild.voice.channel and \
               member_on_guild.voice.channel.id not in HIDDEN_CHANNELS:
                user_still_in_any_visible_vc_on_this_guild = True
        
        if not user_still_in_any_visible_vc_on_this_guild and member.name in USERS:
            try: 
                USERS.remove(member.name)
                log_needs_update_user_list = True
            except ValueError: pass
    elif switched_between_visible_channels: # Auskommentierter Code wieder aktiviert
        await send_log_message(f"🔄 {user_name_log_format} wechselte von ***{before.channel.name}*** zu ***{after.channel.name}***.", target_channel_ids=target_ids_vc)

    if log_needs_update_user_list:
        formatted_current_users = [f"***{u}***" for u in USERS]
        await send_log_message(f"👥 {len(USERS)} Nutzer online: {', '.join(formatted_current_users) if USERS else 'keine'}", target_channel_ids=target_ids_vc)


@tasks.loop(hours=24)
async def msg_purge_task():
    target_ids_task_log = [BOT_AUDIT_ID]

    channel_to_purge_obj = None
    if LOG_CHANNEL_ID:
        channel_to_purge_obj = bot.get_channel(LOG_CHANNEL_ID)
        if not channel_to_purge_obj:
            try:
                channel_to_purge_obj = await bot.fetch_channel(LOG_CHANNEL_ID)
            except Exception as e_fetch_purge:
                await send_log_message(f"⚠️ Tägliches msg_purge: Ziel-Log-Kanal (ID {LOG_CHANNEL_ID}) zum Purgen nicht gefunden: {e_fetch_purge}", target_channel_ids=target_ids_task_log)
                return
    else:
        await send_log_message(f"⚠️ Tägliches msg_purge: LOG_CHANNEL_ID ist nicht konfiguriert. Purge-Task wird übersprungen.", target_channel_ids=target_ids_task_log)
        return

    if not channel_to_purge_obj:
        await send_log_message(f"⚠️ Tägliches msg_purge: Ziel-Log-Kanal (ID {LOG_CHANNEL_ID}) zum Purgen konnte nicht abgerufen werden.", target_channel_ids=target_ids_task_log)
        return

    two_weeks_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(weeks=2)
    def is_older_than_two_weeks(message):
        return message.created_at < two_weeks_ago

    await send_log_message(f"🔄 Starte tägliches msg_purge im Kanal {channel_to_purge_obj.mention} (ID: {LOG_CHANNEL_ID}), um Nachrichten älter als {two_weeks_ago.strftime('%Y-%m-%d %H:%M:%S UTC')} zu löschen (effektiv bis zu 14 Tage alt).", target_channel_ids=target_ids_task_log)
    try:
        deleted_messages = await channel_to_purge_obj.purge(limit=None, check=is_older_than_two_weeks, bulk=True)
        if deleted_messages:
            await send_log_message(f"🗑️ Tägliches msg_purge: {len(deleted_messages)} Nachrichten in {channel_to_purge_obj.mention} gelöscht, die dem Kriterium entsprachen und innerhalb der letzten 14 Tage lagen.", target_channel_ids=target_ids_task_log)
        else:
            await send_log_message(f"ℹ️ Tägliches msg_purge: Keine Nachrichten in {channel_to_purge_obj.mention} gefunden, die gelöscht werden konnten (älter als zwei Wochen und innerhalb der letzten 14 Tage).", target_channel_ids=target_ids_task_log)
    except discord.Forbidden:
        await send_log_message(f"⚠️ Tägliches msg_purge: Keine Berechtigung zum Löschen von Nachrichten in {channel_to_purge_obj.mention}.", target_channel_ids=target_ids_task_log)
    except discord.HTTPException as e:
        if e.status == 400 and "14 days" in e.text.lower():
             await send_log_message(f"ℹ️ Tägliches msg_purge: Konnte keine Nachrichten in {channel_to_purge_obj.mention} löschen. Nachrichten sind möglicherweise alle älter als 14 Tage oder es gab keine zu löschenden Nachrichten. API-Meldung: {e.text}", target_channel_ids=target_ids_task_log)
        else:
             await send_log_message(f"⚠️ Tägliches msg_purge Fehler in {channel_to_purge_obj.mention}: {e}", target_channel_ids=target_ids_task_log)
    except Exception as e:
        await send_log_message(f"⚠️ Tägliches msg_purge Fehler in {channel_to_purge_obj.mention}: {e}", target_channel_ids=target_ids_task_log)


@bot.event
async def on_thread_update(before: Thread, after: Thread):
    if after.guild.id != DISCORD_SERVER_ID: return
    parent = after.parent
    if parent and parent.id == TECHSUPPORT_CHANNEL_ID:
        if not isinstance(parent, discord.ForumChannel): return 
        
        before_tags_lower = {tag.name.lower() for tag in before.applied_tags}
        after_tags_lower = {tag.name.lower() for tag in after.applied_tags}
        closed_tag_lower = CLOSED_TAG_NAME.lower()

        tag_added = closed_tag_lower in after_tags_lower and closed_tag_lower not in before_tags_lower
        
        if tag_added and (not after.locked or not after.archived):
            await send_log_message(f"ℹ️ Thread '{after.name}' (ID: {after.id}) Tag '{CLOSED_TAG_NAME}' erhalten. Schließe...", target_channel_ids=[BOT_AUDIT_ID])
            await close_support_thread(after, f"Tag '{CLOSED_TAG_NAME}' manuell hinzugefügt", set_tag=False)


async def graceful_shutdown():
    global shutdown_initiated
    if shutdown_initiated:
        return
    shutdown_initiated = True
    logger.info("Shutdown-Signal empfangen. Beginne graceful shutdown...")

    if msg_purge_task.is_running():
        logger.info("Stoppe msg_purge_task...")
        msg_purge_task.cancel()
        try:
            # msg_purge_task.stop() ist keine Standardmethode für tasks.loop. cancel() ist korrekt.
            # Wir warten hier nicht explizit, da cancel() den Task beim nächsten Durchlauf beendet.
            # await msg_purge_task.stop() # Entfernt oder durch geeignetes Warten ersetzen falls nötig
            pass # cancel() wurde gerufen, das reicht für den Shutdown-Prozess
        except asyncio.CancelledError:
            logger.info("msg_purge_task erfolgreich abgebrochen.")
        except Exception as e:
            logger.error(f"Fehler beim Stoppen von msg_purge_task: {e}", exc_info=True)
    
    logger.info("Sende 'Bot wird gestoppt...' Nachricht (falls möglich).")
    stop_message_targets = []
    if LOG_CHANNEL_ID: stop_message_targets.append(LOG_CHANNEL_ID)
    if BOT_AUDIT_ID: stop_message_targets.append(BOT_AUDIT_ID)
    
    if stop_message_targets:
        try:
            if bot.is_ready() or (bot.loop and bot.loop.is_running() and not bot.is_closed()):
                await send_log_message(
                    "⏳ Bot wird gestoppt...",
                    target_channel_ids=list(set(stop_message_targets))
                )
                logger.info("'Bot wird gestoppt...' Nachricht gesendet.")
                await asyncio.sleep(0.5) 
            else:
                logger.warning("Bot nicht bereit, 'Bot wird gestoppt...' Nachricht kann nicht gesendet werden.")
        except Exception as e:
            logger.error(f"Fehler beim Senden der 'Bot wird gestoppt...' Nachricht: {e}", exc_info=True)
    
    logger.info("Schließe Bot-Verbindung...")
    if bot.loop and bot.loop.is_running() and not bot.is_closed():
        try:
            await bot.close()
            logger.info("Bot-Verbindung erfolgreich geschlossen.")
        except Exception as e:
            logger.error(f"Fehler beim bot.close(): {e}", exc_info=True)
    else:
        logger.info("Bot-Verbindung war bereits geschlossen oder Loop nicht aktiv.")
    logger.info("Graceful shutdown abgeschlossen.")


def handle_signal(signum, frame):
    global shutdown_initiated
    if shutdown_initiated:
        logger.info(f"Signal {signum} erneut empfangen, Shutdown bereits eingeleitet.")
        return
    
    signal_name = signal.Signals(signum).name if isinstance(signum, int) else str(signum)
    logger.info(f"Signal {signal_name} empfangen. Leite graceful shutdown ein.")
    
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(graceful_shutdown(), bot.loop)
    else:
        logger.warning("Bot-Loop nicht aktiv. Direkter Versuch, Shutdown-Flag zu setzen und zu beenden.")
        if not shutdown_initiated: 
            shutdown_initiated = True

async def main():
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        logger.critical("Fehler: Umgebungsvariable 'DISCORD_TOKEN' ist nicht gesetzt.")
        return

    try:
        logger.info("Starte Bot...")
        await bot.start(TOKEN)
    except discord.LoginFailure:
        logger.critical("Login fehlgeschlagen. Überprüfe den Token.")
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt während bot.start() in main().")
        if not shutdown_initiated:
            await graceful_shutdown()
    except Exception as e:
        logger.critical(f"Unerwarteter Fehler beim Starten oder während der Laufzeit des Bots in main(): {e}", exc_info=True)
        if not shutdown_initiated:
            logger.info("Versuche graceful shutdown nach unerwartetem Fehler in main()...")
            try:
                await graceful_shutdown()
            except Exception as eshutdown:
                logger.error(f"Fehler während des Shutdowns nach Fehler in main(): {eshutdown}", exc_info=True)
    finally:
        if not bot.is_closed() and not shutdown_initiated:
            logger.warning("Bot war noch nicht geschlossen und kein Shutdown eingeleitet. Schließe jetzt...")
            await graceful_shutdown() 
        elif not bot.is_closed() and shutdown_initiated:
             logger.info("Bot war trotz eingeleitetem Shutdown noch nicht geschlossen. Erneuter Versuch via bot.close().")
             if bot.loop and bot.loop.is_running(): # Nur wenn Loop noch läuft
                 await bot.close()
             else:
                 logger.warning("Bot-Loop nicht aktiv, bot.close() im finalen Block übersprungen.")


        logger.info("Bot-Hauptroutine (main) beendet.")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt auf oberster Ebene empfangen. Programm wird beendet.")
    finally:
        logger.info("asyncio.run() wurde beendet. Programm-Aufräumarbeiten abgeschlossen.")
        logger.info("Bot-Prozess wird nun endgültig beendet.")

# --------- User Log Database Initialization ---------
def init_user_log_db():
    try:
        conn = sqlite3.connect('user_log.db')
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_joins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                channel_id INTEGER,
                channel_name TEXT,
                timestamp TEXT
            )
        """)
        conn.commit()
        logger.info("User log database initialized successfully (user_log.db and user_joins table).")
    except sqlite3.Error as e:
        logger.error(f"SQLite error during user_log_db initialization: {e}")
    finally:
        if conn:
            conn.close()
# Ensure newline at the end of the file
# v1.3
import os
import threading
import datetime
import random # Hinzugefügt für zufällige Bildauswahl
from flask import Flask
import discord
from discord import Intents, Thread, File, Embed # Embed hinzugefügt
from discord.ext import commands, tasks
from typing import Dict, List, Optional

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
intents.message_content = True # Wichtig für das Lesen von Befehlsinhalten
intents.guild_messages = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!!", intents=intents)

# Konfiguration
message_delete_count = 15 # Standard für msg_purge_task, nicht für delete-Befehl
DISCORD_SERVER_ID = 374159356717039616
TECHSUPPORT_CHANNEL_ID = 1139952610883928134
CLOSED_TAG_NAME = "🔒 CLOSED"
LOG_CHANNEL_ID = 1266773678306230374
HIDDEN_CHANNELS = [1255930025463644232, 1233872680680296499, 374159356717039620]
USERS: List[str] = []
IMAGES_FOLDER = "images" # Unterordner für die Bilder

async def send_log_message(msg: str, embed: Optional[Embed] = None):
    try:
        log_channel_obj = bot.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
        if log_channel_obj:
            await log_channel_obj.send(msg, embed=embed)
        else:
            print(f"Log-Kanal {LOG_CHANNEL_ID} nicht gefunden. Nachricht: {msg}")
    except Exception as e:
        print(f"Fehler beim Senden der Log-Nachricht: {e}")

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
                        print(f"Fehler beim Setzen des Tags für Thread '{thread.name}': {e}")
                        await send_log_message(f"⚠️ Fehler beim Setzen des Tags '{CLOSED_TAG_NAME}' für Thread '{thread.name}': {e.text}")
            else:
                print(f"Warnung: Der Tag '{CLOSED_TAG_NAME}' wurde im Forum '{thread.parent.name}' nicht gefunden.")
                await send_log_message(f"⚠️ Warnung: Tag '{CLOSED_TAG_NAME}' im Forum '{thread.parent.name}' nicht gefunden für Thread '{thread.name}'.")

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
            print(f"Thread '{thread.name}' wurde durch {trigger_source} {action_str}.")
            await send_log_message(f"🧵 Thread '{thread.name}' (ID: {thread.id}) durch '{trigger_source}' {action_str}.")

    except discord.Forbidden:
        err_msg = f"Fehler: Keine Berechtigung, den Thread '{thread.name}' zu bearbeiten (sperren/Tag/archivieren)."
        print(err_msg)
        await send_log_message(f"⚠️ {err_msg}")
        try:
            await thread.send(f"Fehler: Ich habe nicht die nötigen Berechtigungen, um diesen Thread zu sperren, den Tag zu setzen oder zu archivieren. Bitte überprüfe meine Rollen und Berechtigungen im Kanal '{thread.parent.name}'.")
        except Exception:
            pass
    except Exception as e:
        print(f"Generischer Fehler beim Schließen des Threads '{thread.name}': {e}")
        await send_log_message(f"⚠️ Fehler beim Schließen des Threads '{thread.name}' (ID: {thread.id}): {e}")

@bot.event
async def on_ready():
    print(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    # Sicherstellen, dass der images-Ordner existiert
    if not os.path.exists(IMAGES_FOLDER):
        os.makedirs(IMAGES_FOLDER)
        print(f"Ordner '{IMAGES_FOLDER}' wurde erstellt. Bitte füge Bilder hinzu.")
        await send_log_message(f"⚠️ Ordner '{IMAGES_FOLDER}' wurde erstellt. Bitte Bilder für den `delete`-Befehl hinzufügen.")

    threading.Thread(target=run_flask, daemon=True).start()
    print("Flask-Server läuft für Health Checks.")

    try:
        # STRATEGIE: Synchronisiere mit einem bestimmten Server für schnelle Updates.
        # Dies ist für Hybridbefehle (die global definiert sind) wichtig,
        # damit sie auf dem Testserver sofort erscheinen.
        guild_obj = discord.Object(id=DISCORD_SERVER_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        synced_commands = await bot.tree.sync(guild=guild_obj)
        
        # ALTERNATIV: Wenn Sie wirklich globale Befehle wollen und bereit sind,
        # bis zu einer Stunde auf die Verbreitung zu warten:
        # synced_commands = await bot.tree.sync() # Dies wäre der EINZIGE Sync-Aufruf.

        num_synced = len(synced_commands) if synced_commands else 0
        command_names = [cmd.name for cmd in synced_commands] if synced_commands else []
        
        print(f"{num_synced} Befehle für Guild {DISCORD_SERVER_ID} synchronisiert: {command_names}")
        await send_log_message(f"✅ Bot gestartet. {num_synced} Befehle für Guild {DISCORD_SERVER_ID} synchronisiert: {command_names}")

    except Exception as e:
        print(f"Fehler beim Synchronisieren der Befehle: {e}")
        await send_log_message(f"⚠️ Bot gestartet, aber Fehler beim Synchronisieren der Befehle: {e}")

    if not msg_purge_task.is_running():
        msg_purge_task.start()

    USERS.clear()
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if vc.id not in HIDDEN_CHANNELS:
                for member in vc.members:
                    if not member.bot and member.name not in USERS:
                        USERS.append(member.name)
    USERS.sort()

    formatted_users = [f"***{u}***" for u in USERS]
    user_list_msg = f"👥 {len(USERS)} Nutzer online (beim Start): {', '.join(formatted_users) if USERS else 'keine'}"
    await send_log_message(user_list_msg)

    try:
        tech_support_forum = bot.get_channel(TECHSUPPORT_CHANNEL_ID) or await bot.fetch_channel(TECHSUPPORT_CHANNEL_ID)
        if isinstance(tech_support_forum, discord.ForumChannel):
            closed_tag_obj_on_ready = await get_forum_tag_by_name(tech_support_forum, CLOSED_TAG_NAME)
            if not closed_tag_obj_on_ready:
                await send_log_message(f"⚠️ WICHTIG: Der Tag '{CLOSED_TAG_NAME}' konnte im Forum '{tech_support_forum.name}' (ID: {tech_support_forum.id}) nicht gefunden werden. Die automatische Schließung per Tag funktioniert nicht korrekt.")
        elif tech_support_forum:
            await send_log_message(f"⚠️ Tech-Support-Kanal {TECHSUPPORT_CHANNEL_ID} ('{tech_support_forum.name}') ist kein Forum-Kanal.")
        else:
            await send_log_message(f"⚠️ Tech-Support-Kanal {TECHSUPPORT_CHANNEL_ID} konnte nicht gefunden werden.")
    except Exception as e:
        print(f"Fehler bei der initialen Thread-Verarbeitung (on_ready): {e}")
        await send_log_message(f"⚠️ Fehler bei der initialen Thread-Verarbeitung (on_ready): {e}")

# --- Hybrid Commands ---
@bot.hybrid_command(name="close", description="Schließt den aktuellen Support-Thread.")
async def close(ctx: commands.Context):
    if not (isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id == TECHSUPPORT_CHANNEL_ID):
        await ctx.send("Dieser Befehl kann nur in einem Support-Thread des Tech-Support-Forums verwendet werden.", ephemeral=True)
        return
    thread = ctx.channel
    # ... (Rest des close Befehls bleibt gleich) ...
    forum_channel = thread.parent
    if not isinstance(forum_channel, discord.ForumChannel):
        await ctx.send("Fehler: Der übergeordnete Kanal ist kein Forum-Kanal. Kann den Tag nicht verwalten.", ephemeral=True)
        return
    closed_tag_object = await get_forum_tag_by_name(forum_channel, CLOSED_TAG_NAME)
    if not closed_tag_object:
        await ctx.send(f"Warnung: Der Tag '{CLOSED_TAG_NAME}' wurde im Forum nicht gefunden. Der Thread wird gesperrt und archiviert, aber der Tag kann nicht gesetzt werden.", ephemeral=True)
        await send_log_message(f"⚠️ Warnung bei Befehl `close` in Thread '{thread.name}': Tag '{CLOSED_TAG_NAME}' im Forum nicht gefunden.")
    has_closed_tag = any(tag.id == closed_tag_object.id for tag in thread.applied_tags) if closed_tag_object else False
    already_fully_closed = thread.locked and (has_closed_tag if closed_tag_object else True) and thread.archived
    if already_fully_closed:
        await ctx.send("Dieser Thread ist bereits als geschlossen markiert (gesperrt, getaggt, archiviert).", ephemeral=True)
        return
    if thread.locked and (has_closed_tag if closed_tag_object else True) and not thread.archived:
        await ctx.send("Dieser Thread ist bereits gesperrt und getaggt, wird nun zusätzlich archiviert.", ephemeral=True)
        try:
            await thread.edit(archived=True)
            await send_log_message(f"ℹ️ Thread '{thread.name}' war gesperrt/getagged, aber nicht archiviert. Jetzt archiviert nach `close`-Befehl von {ctx.author.mention}.")
        except Exception as e:
            await send_log_message(f"⚠️ Fehler beim erneuten Archivieren von Thread '{thread.name}': {e}")
        return
    trigger_name = ctx.author.mention if ctx.author else "einem unbekannten Benutzer"
    trigger = f"Befehl `/{ctx.invoked_with}` von {trigger_name}" if ctx.interaction else f"Befehl `{bot.command_prefix}{ctx.invoked_with}` von {trigger_name}"
    await ctx.send("Der Schließvorgang für den Thread wird eingeleitet...", ephemeral=True)
    await close_support_thread(thread, trigger_source=trigger, set_tag=True)


@bot.hybrid_command(name="delete", description="Sendet eine Info-Nachricht und löscht dann Nachrichten im aktuellen Kanal.")
@commands.has_permissions(manage_messages=True)
@commands.guild_only()
async def delete(ctx: commands.Context, anzahl: int = 10): # Standard-Anzahl angepasst
    if not (0 < anzahl <= 100): # Inklusive 0 oder negative Zahlen abfangen
        await ctx.send("Bitte gib eine Zahl zwischen 1 und 100 für die zu löschenden Nachrichten an.", ephemeral=True)
        return

    target_channel = ctx.channel
    if not isinstance(target_channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
        await ctx.send("Dieser Befehl kann nur in Textkanälen, Voice-Kanal-Chats oder Threads verwendet werden.", ephemeral=True)
        return

    # 1. Bild auswählen und Embed vorbereiten
    image_file_to_send = None
    image_name_for_embed = None
    try:
        available_images = [f for f in os.listdir(IMAGES_FOLDER) if os.path.isfile(os.path.join(IMAGES_FOLDER, f))]
        if not available_images:
            await ctx.send(f"Keine Bilder im Ordner '{IMAGES_FOLDER}' gefunden. Bitte füge welche hinzu.", ephemeral=True)
            await send_log_message(f"⚠️ Versuchter `delete`-Befehl, aber keine Bilder in '{IMAGES_FOLDER}' durch {ctx.author.mention} in #{target_channel.name}.")
            return
        
        chosen_image_name = random.choice(available_images)
        image_path = os.path.join(IMAGES_FOLDER, chosen_image_name)
        image_file_to_send = discord.File(image_path, filename=chosen_image_name) # Wichtig: filename für Embed
        image_name_for_embed = chosen_image_name # Für `set_image(url=f"attachment://{image_name_for_embed}")`

    except FileNotFoundError:
        await ctx.send(f"Fehler: Der Bilderordner '{IMAGES_FOLDER}' wurde nicht gefunden.", ephemeral=True)
        await send_log_message(f"⚠️ Bilderordner '{IMAGES_FOLDER}' nicht gefunden bei `delete`-Befehl durch {ctx.author.mention} in #{target_channel.name}.")
        return
    except Exception as e:
        await ctx.send("Ein Fehler ist bei der Bildauswahl aufgetreten.", ephemeral=True)
        await send_log_message(f"⚠️ Fehler bei Bildauswahl für `delete` durch {ctx.author.mention} in #{target_channel.name}: {e}")
        return

    # Embed erstellen
    embed = Embed(description="Delet this", color=discord.Color.blue()) # Blaue Farbe für den "Weiterleitungs"-Balken

    
    if image_name_for_embed:
         embed.set_image(url=f"attachment://{image_name_for_embed}")


    # 2. Info-Nachricht mit Bild senden
    try:
        # Die auslösende Nachricht (ctx.message) wird NICHT direkt gelöscht,
        # aber wir müssen sie bei der Zählung für purge berücksichtigen.
        # Da wir die Info-Nachricht *vor* dem Purge senden, wird sie nicht mitgepurged,
        # außer wenn `anzahl` sehr klein ist.
        
        # Sende zuerst die ephemere Bestätigung an den User
        await ctx.send(f"Info-Nachricht wird gesendet und {anzahl} vorherige Nachrichten werden gelöscht...", ephemeral=True, delete_after=10)

        # Sende die öffentliche "Delet this" Nachricht
        info_message = await target_channel.send(file=image_file_to_send, embed=embed)

    except discord.Forbidden:
        await ctx.send("Ich habe keine Berechtigung, Nachrichten oder Bilder in diesem Kanal zu senden.", ephemeral=True)
        await send_log_message(f"⚠️ Keine Sende-Berechtigung für `delete`-Info in #{target_channel.name} (Versuch von {ctx.author.mention}).")
        return
    except Exception as e:
        await ctx.send(f"Ein Fehler ist beim Senden der Info-Nachricht aufgetreten: {e}", ephemeral=True)
        await send_log_message(f"⚠️ Fehler beim Senden der `delete`-Info in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}")
        return

    # 3. Nachrichten löschen (die auslösende Nachricht des Benutzers ausschließen)
    deleted_messages_count = 0
    try:
        # Wir wollen `anzahl` Nachrichten VOR der Befehlsnachricht löschen.
        # `purge` löscht Nachrichten, die ÄLTER sind als die letzte Nachricht (oder die angegebenen `before` Nachricht).
        # Die `ctx.message` ist die Befehlsnachricht.
        
        # Check-Funktion, um die Befehlsnachricht und die soeben gesendete Info-Nachricht des Bots zu überspringen
        def check(m):
            return m.id != ctx.message.id and m.id != info_message.id

        # Wichtig: `limit` in `purge` ist die *maximale* Anzahl.
        # Wir wollen `anzahl` Nachrichten löschen, die vor der Befehlsnachricht `ctx.message` kamen.
        # Da `purge` rückwärts zählt, ist das Verhalten hier meistens intuitiv.
        deleted_messages = await target_channel.purge(limit=anzahl, check=check, before=ctx.message)
        deleted_messages_count = len(deleted_messages)

        log_msg_text = f"🗑️ {deleted_messages_count} Nachrichten in Kanal #{target_channel.name} (ID: {target_channel.id}) durch {ctx.author.mention} gelöscht (nach Info-Post)."
        await send_log_message(log_msg_text)
        print(f"{deleted_messages_count} Nachrichten in #{target_channel.name} durch {ctx.author} gelöscht.")

    except discord.Forbidden:
        err_msg_user = "Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu löschen."
        # Info-Nachricht wurde evtl. schon gesendet, hier nur User-Feedback.
        # Ein `await ctx.send` hier könnte fehlschlagen, wenn die Interaktion schon beantwortet wurde.
        # `ctx.followup.send` ist sicherer für ephemere Nachrichten nach der initialen Antwort.
        try:
            await ctx.followup.send(err_msg_user, ephemeral=True)
        except discord.HTTPException: # Falls die Interaktion zu alt ist
             await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)

        await send_log_message(f"⚠️ Keine Lösch-Berechtigung in #{target_channel.name} (Versuch von {ctx.author.mention}).")
    except Exception as e:
        err_msg_user = f"Ein Fehler ist beim Löschen der Nachrichten aufgetreten: {e}"
        try:
            await ctx.followup.send(err_msg_user, ephemeral=True)
        except discord.HTTPException:
            await target_channel.send(f"{ctx.author.mention}, {err_msg_user}", delete_after=15)
        await send_log_message(f"⚠️ Fehler beim Löschen in #{target_channel.name} (Versuch von {ctx.author.mention}): {e}")


@delete.error
async def delete_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Du hast nicht die erforderlichen Berechtigungen, um diesen Befehl auszuführen.", ephemeral=True)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Dieser Befehl kann nicht in privaten Nachrichten verwendet werden.", ephemeral=True)
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.HTTPException) and error.original.status == 404:
        await ctx.send("Fehler: Der Kanal konnte nicht gefunden oder Nachrichten darin nicht gelöscht werden (HTTP 404).", ephemeral=True)
    else:
        await ctx.send(f"Ein Fehler ist im `delete`-Befehl aufgetreten: {error}", ephemeral=True)
    print(f"Fehler im delete-Befehl von {ctx.author}: {error}")


@bot.hybrid_command(name="users", description="Listet alle Benutzer in den sichtbaren Voice-Channels auf.")
@commands.guild_only()
async def users(ctx: commands.Context):
    # ... (Rest des users Befehls bleibt gleich) ...
    output_lines = ["Aktive Benutzer in Voice-Channels:"]
    any_users_found = False
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
        await ctx.defer(ephemeral=False)
        parts = []
        current_part = ""
        for line in output_lines:
            if len(current_part) + len(line) + 1 > 1980:
                parts.append(current_part)
                current_part = line
            else:
                if current_part:
                    current_part += "\n" + line
                else:
                    current_part = line
        if current_part:
            parts.append(current_part)
        first_sent = False
        for i, part_msg in enumerate(parts):
            if not first_sent:
                await ctx.send(part_msg, ephemeral=False)
                first_sent = True
            else:
                await ctx.followup.send(part_msg, ephemeral=False)
    else:
        await ctx.send(message_to_send, ephemeral=False)

# --- Event Handler ---
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot:
        return
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # ... (Rest des on_voice_state_update Handlers bleibt gleich) ...
    if member.bot: return
    user_name_log_format = f"***{member.name}***"
    log_needs_update_user_list = False
    joined_visible_channel = after.channel and after.channel.id not in HIDDEN_CHANNELS and \
                             (not before.channel or before.channel.id in HIDDEN_CHANNELS)
    left_visible_channel = before.channel and before.channel.id not in HIDDEN_CHANNELS and \
                           (not after.channel or after.channel.id in HIDDEN_CHANNELS)
    switched_between_visible_channels = before.channel and before.channel.id not in HIDDEN_CHANNELS and \
                                       after.channel and after.channel.id not in HIDDEN_CHANNELS and \
                                       before.channel.id != after.channel.id
    if joined_visible_channel:
        ch_name_log_format = f"***{after.channel.name}***"
        await send_log_message(f"➕ {user_name_log_format} hat {ch_name_log_format} betreten.")
        if member.name not in USERS:
            USERS.append(member.name)
            USERS.sort()
            log_needs_update_user_list = True
    elif left_visible_channel:
        ch_name_log_format = f"***{before.channel.name}***"
        await send_log_message(f"➖ {user_name_log_format} hat {ch_name_log_format} verlassen.")
        user_still_in_any_visible_vc = False
        for guild_check in bot.guilds:
            member_on_guild = guild_check.get_member(member.id)
            if member_on_guild and member_on_guild.voice and member_on_guild.voice.channel and \
               member_on_guild.voice.channel.id not in HIDDEN_CHANNELS:
                user_still_in_any_visible_vc = True
                break
        if not user_still_in_any_visible_vc and member.name in USERS:
            try: USERS.remove(member.name); log_needs_update_user_list = True
            except ValueError: pass
    elif switched_between_visible_channels:
        await send_log_message(f"🔄 {user_name_log_format} wechselte von ***{before.channel.name}*** zu ***{after.channel.name}***.")
    if log_needs_update_user_list:
        formatted_current_users = [f"***{u}***" for u in USERS]
        await send_log_message(f"👥 {len(USERS)} Nutzer online: {', '.join(formatted_current_users) if USERS else 'keine'}")


@tasks.loop(hours=24)
async def msg_purge_task():
    # ... (Rest Ihrer msg_purge_task, falls vorhanden, bleibt gleich) ...
    log_channel_obj = bot.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
    if not log_channel_obj:
        # Loggen, dass der Kanal nicht gefunden wurde, falls send_log_message hier verfügbar ist
        # await send_log_message(f"⚠️ Tägliches msg_purge: Log-Kanal mit ID {LOG_CHANNEL_ID} nicht gefunden.")
        print(f"⚠️ Tägliches msg_purge: Log-Kanal mit ID {LOG_CHANNEL_ID} nicht gefunden.") # Fallback, falls send_log_message nicht global ist
        return

    # Berechne den Zeitpunkt vor zwei Wochen
    # Discord Nachrichten haben Zeitstempel in UTC
    two_weeks_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(weeks=2)

    # Definiere eine Check-Funktion, die prüft, ob eine Nachricht älter als zwei Wochen ist
    def is_older_than_two_weeks(message):
        # message.created_at ist bereits ein aware datetime-Objekt (UTC)
        return message.created_at < two_weeks_ago

    await send_log_message(f"🔄 Starte tägliches msg_purge im Log-Kanal ({log_channel_obj.mention}), um Nachrichten älter als {two_weeks_ago.strftime('%Y-%m-%d %H:%M:%S UTC')} zu löschen.")

    try:
        # Lösche Nachrichten, die älter als zwei Wochen sind.
        # Kein 'limit' mehr nötig, da wir alle passenden Nachrichten löschen wollen.
        # Die 'purge'-Methode kann Nachrichten löschen, die älter als 14 Tage sind, wenn ein 'check' verwendet wird.
        deleted_messages = await log_channel_obj.purge(check=is_older_than_two_weeks)

        if deleted_messages:
            await send_log_message(f"🗑️ Tägliches msg_purge: {len(deleted_messages)} Nachrichten in {log_channel_obj.mention} gelöscht, die älter als zwei Wochen waren.")
        else:
            await send_log_message(f"ℹ️ Tägliches msg_purge: Keine Nachrichten älter als zwei Wochen zum Löschen in {log_channel_obj.mention} gefunden.")
    except discord.Forbidden:
        await send_log_message(f"⚠️ Tägliches msg_purge: Keine Berechtigung zum Löschen von Nachrichten in {log_channel_obj.mention}.")
    except Exception as e:
        await send_log_message(f"⚠️ Tägliches msg_purge Fehler in {log_channel_obj.mention}: {e}")


@bot.event
async def on_thread_update(before: Thread, after: Thread):
    # ... (Rest des on_thread_update Handlers bleibt gleich) ...
    parent = after.parent
    if parent and parent.id == TECHSUPPORT_CHANNEL_ID:
        if not isinstance(parent, discord.ForumChannel): return
        before_tags_lower = {tag.name.lower() for tag in before.applied_tags}
        after_tags_lower = {tag.name.lower() for tag in after.applied_tags}
        closed_tag_lower = CLOSED_TAG_NAME.lower()
        tag_added = closed_tag_lower in after_tags_lower and closed_tag_lower not in before_tags_lower
        if tag_added and (not after.locked or not after.archived):
            await send_log_message(f"ℹ️ Thread '{after.name}' (ID: {after.id}) Tag '{CLOSED_TAG_NAME}' erhalten. Schließe...")
            await close_support_thread(after, f"Tag '{CLOSED_TAG_NAME}' hinzugefügt", set_tag=False)

if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Fehler: Umgebungsvariable 'DISCORD_TOKEN' ist nicht gesetzt.")
    else:
        bot.run(TOKEN)

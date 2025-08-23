import io
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure ffmpeg/ffprobe BEFORE importing pydub so that pydub picks it up
FFMPEG_BIN_DIR = r"C:\Users\chris\Cursor\discord-voice-transcript-for-teams\ffmpeg-master-latest-win64-gpl\bin"
ffmpeg_path = str(Path(FFMPEG_BIN_DIR) / "ffmpeg.exe")
ffprobe_path = str(Path(FFMPEG_BIN_DIR) / "ffprobe.exe")
os.environ["PATH"] = FFMPEG_BIN_DIR + ";" + os.environ.get("PATH", "")
os.environ["FFMPEG_BINARY"] = ffmpeg_path
os.environ["FFPROBE_BINARY"] = ffprobe_path

import pydub  # pip install pydub==0.25.1

import discord
from discord.sinks import MP3Sink
from pydub import AudioSegment

# Also set on the AudioSegment in case environment vars are ignored by utils
AudioSegment.converter = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path


bot = discord.Bot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


async def finished_callback(sink: MP3Sink, channel: discord.TextChannel):
    mention_strs = []
    audio_segs: list[pydub.AudioSegment] = []

    longest = pydub.AudioSegment.empty()

    for user_id, audio in sink.audio_data.items():
        mention_strs.append(f"<@{user_id}>")

        seg = pydub.AudioSegment.from_file(audio.file, format="mp3")

        # Determine the longest audio segment
        if len(seg) > len(longest):
            audio_segs.append(longest)
            longest = seg
        else:
            audio_segs.append(seg)

    for seg in audio_segs:
        longest = longest.overlay(seg)

    # Always save recordings locally
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("data/garmin-output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save individual user recordings
    for user_id, audio in sink.audio_data.items():
        audio.file.seek(0)
        user_filename = f"{timestamp}_{user_id}.mp3"
        user_filepath = output_dir / user_filename
        
        with open(user_filepath, "wb") as f:
            f.write(audio.file.read())
        print(f"Saved user recording: {user_filepath}")
    
    # Save mixed recording
    mixed_filename = f"{timestamp}_mixed_recording.mp3"
    mixed_filepath = output_dir / mixed_filename
    
    with io.BytesIO() as f:
        longest.export(f, format="mp3")
        f.seek(0)
        with open(mixed_filepath, "wb") as output_file:
            output_file.write(f.read())
    
    print(f"Saved mixed recording: {mixed_filepath}")
    
    # Send confirmation message to chat
    await channel.send(
        f"Finished! Recorded audio for {', '.join(mention_strs)}. "
        f"Recordings saved locally in `/data/garmin-output/`"
    )


@bot.command()
async def join(ctx: discord.ApplicationContext):
    """Join the voice channel!"""
    voice = ctx.author.voice

    if not voice:
        return await ctx.respond("You're not in a vc right now")

    await voice.channel.connect()

    vc: discord.VoiceClient = ctx.voice_client
    vc.start_recording(
        MP3Sink(),
        finished_callback,
        ctx.channel,
        sync_start=True,  # WARNING: This feature is very unstable and may break at any time.
    )
    await ctx.respond("Joined and started recording!")


@bot.command()
async def stop(ctx: discord.ApplicationContext):
    """Stop the recording"""
    vc: discord.VoiceClient = ctx.voice_client

    if not vc:
        return await ctx.respond("There's no recording going on right now")

    vc.stop_recording()

    await ctx.respond("The recording has stopped!")


@bot.command()
async def leave(ctx: discord.ApplicationContext):
    """Leave the voice channel!"""
    vc: discord.VoiceClient = ctx.voice_client

    if not vc:
        return await ctx.respond("I'm not in a vc right now")

    await vc.disconnect()

    await ctx.respond("Left!")


# Get token from environment variable
token = os.getenv('DISCORD_TOKEN')
if not token:
    print("Error: DISCORD_TOKEN not found in environment variables or .env file")
    print("Please create a .env file with DISCORD_TOKEN=your_bot_token_here")
    exit(1)

bot.run(token)


import os

import discord
from discord.ext import tasks

#from discord.ext import commands
from keep_up import keep_awake

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
users = []
hidden_channels = [1255930025463644232, 1233872680680296499]


## JOIN EVENTS ##
@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))
    msg_purge.start()
    user_list.start()
    await send_msg(1266773678306230374, "✅ Bot starting ✅")
    await send_msg(1266773678306230374, "✅ Bot started ✅")


async def send_msg(channel, msg):
    channel = client.get_channel(channel)
    print("send_msg inm 6 sec")
    # for i in range(2):
    #     print(i)
    #     time.sleep(i)
    #     i += 1
    print("msg")
    await channel.send(f'{msg}')


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('$hello'):
        await message.channel.send('Hello!')


@client.event
async def on_voice_state_update(member, before, after):
    # print(member)
    # print(before.channel)
    # print(after.channel)

    #Server joinen
    if before.channel is None or before.channel.id in hidden_channels and after.channel is not None:
        print(f'{member} has joined {after.channel}')

        if after.channel.id not in hidden_channels:
            #await member.guild.system_channel.send("Alarm!")
            channel = client.get_channel(1266773678306230374)

            await channel.send(
                f'➕ ***{member}*** joined ***{after.channel}*** ➕✅')
            # users.append(member.name, " in ", after.channel)
            #if after.channel.id == 1107424487747616868:
            #await member.guild.system_channel.send("Alar!")
            #print(channel.id)
            print("adding", member.name)
            users.append(member.name)
            await channel.send(f' Current Users Online: ***{users}*** online')

    # Server leaven
    if before.channel is not None and after.channel is None or after.channel.id in hidden_channels:
        print(f'{member} has leaved {before.channel}')
        channel = client.get_channel(1266773678306230374)
        # if before.channel.id != 1255930025463644232:
        #     print("channel", before.channel, "ID", before.channel.id)
        if before.channel.id not in hidden_channels:
            await channel.send(
                f'➖ ***{member}*** leaved ***{before.channel}*** ➖❌')
            # users.remove(member.name + " in " + after.channel)#
            print("removing", member.name)
            try:
                users.remove(member.name)
            except ValueError as e:
                print(e)
            await channel.send(f' Current Users Online: ***{users}*** online')


@tasks.loop(hours=24)
async def msg_purge():
    channel = client.get_channel(1266773678306230374)
    print("Deleting last 50 messages")
    # await send_msg(1266773678306230374, "Deleting last 50 messages")
    await channel.purge(limit=50)


@tasks.loop(hours=24)
async def user_list():
    channel = client.get_channel(1266773678306230374)
    print("Current Users online:")
    # await channel.purge(limit=50)
    print(users)
    await channel.send(f' Current Users Online: {users} online')
    msg = "Current Users Online:\n"
    for user in users:
        msg += (f' ✅{user} in {after.channel}✅\n')


## END JOIN EVENTS ##
keep_awake()
try:
    token = os.getenv("TOKEN") or ""
    if token == "":
        raise Exception("Please add your token to the Secrets pane.")
    # client2.run(token)
    client.run(token)

except discord.HTTPException as e:
    if e.status == 429:
        print(
            "The Discord servers denied the connection for making too many requests"
        )
        print(
            "Get help from https://stackoverflow.com/questions/66724687/in-discord-py-how-to-solve-the-error-for-toomanyrequests"
        )
    else:
        raise e

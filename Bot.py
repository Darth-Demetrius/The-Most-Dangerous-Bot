#Lord Prince Earl von Dukeington#6152

import os
import discord
from dotenv import load_dotenv
from Location_Data import *
from Command_List import *

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = int(os.getenv('GUILD_TOKEN'))

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    guild = client.get_guild(GUILD)
    print(
        f'{client.user} is connected to:',
        f'{guild.name} - id: {guild.id}',
        sep='\n')
    
#    members = "\n - ".join([str(member.name)+": "+str(member.nick) for member in guild.members])
#    print(f"Guild Members:\n - {members}")

#================   On Member Join      ================
@client.event
async def on_member_join(member):
    guild = discord.utils.get(client.guilds, id=GUILD)
    await member.add_roles(guild.get_role(783900909578747944)) #citizen
    if member.id == 343986320579887105 or member.id == 344174292004765697:
        await member.add_roles(guild.get_role(784262916844683285)) #Tech Support
    
    await member.create_dm()
    await member.dm_channel.send(f'Hi {member.name}, welcome to {guild.name}')

#================   On Reaction Add     ================
@client.event
async def on_raw_reaction_add(payload):
    guild = client.get_guild(payload.guild_id)
    message = await guild.get_channel(payload.channel_id).fetch_message(payload.message_id)
    author = client.get_user(payload.user_id)
    
#    if user == client.user:
#        return

#announcements
    if message.channel.name == "public-announcements":
        
        #/start
        if payload.emoji.name == '☑️' or payload.emoji.name == '✅': #checkboxes 2611 2705
            DM   = guild.get_role(783900332757352458)
            hunt = guild.get_role(783900808039104572)
            fugi = guild.get_role(783900625544413214)
            play = guild.get_role(784287952012050452)
            citi = guild.get_role(783900909578747944)
            
            for user in guild.members:
                user.remove_roles(DM, hunt, fugi, reason="Game start")
                user.add_roles(citi, reason="Game start")
            
            for reaction in message.reactions:
                async for user in reaction.users():
                    if user == client.user: continue
                    user = guild.get_member(user.id)
                    if str(reaction) == '\U0001F1E9': #D
                        await user.add_roles(DM, reason="Game start")
                        await user.remove_roles(citi, reason="Game start")
                    if reaction == '\U0001F1EB': #F
                        await user.add_roles(fugi, reason="Game start")
                        await user.remove_roles(citi, reason="Game start")
                    if reaction == '\U0001F1ED': #H
                        await user.add_roles(hunt, reason="Game start")
                        await user.remove_roles(citi, reason="Game start")
            return
        
        #cleanup
        if author != client.user:
            for reaction in message.reactions:
                if reaction.count > 1 and reaction.me: await reaction.remove(client.user)
                if str(reaction.emoji) != str(payload.emoji):
                    async for user in reaction.users():
                        if user == author: await reaction.remove(user)
        return
    
#================   On Reaction Remove  ================
@client.event
async def on_raw_reaction_remove(payload):
    guild = client.get_guild(payload.guild_id)
    message = await guild.get_channel(payload.channel_id).fetch_message(payload.message_id)
    
    #cleanup
    if message.channel.name == "public-announcements":
        for reaction in message.reactions:
            if str(reaction.emoji) == str(payload.emoji): return
        await message.add_reaction(payload.emoji)


#================   On Message          ================
@client.event
async def on_message(message):
    if message.author == client.user: return

#bot commands
    if message.content[0] == '/':
        content = message.content.split(' ')

        #help
        if content[0] == "/help":
            await message.delete()
            await message.channel.send(help())
            return

        if content[0] == "/start":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            
            msg = await client.get_channel(783883079449313280).history(limit=1).flatten()
            await msg[0].add_reaction('✅')
            
            await message.delete()
            await client.get_channel(783883079449313280).send(
                    "Roles have been assigned. Let the game commence!") #public-announcements
            return

        #announce
        if content[0] == "/announce":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            
            await message.delete()
            await client.get_channel(783883079449313280).send(message.content[9:])
            return

        #begin
        if content[0] == "/begin":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            
            await message.delete()
            msg = message.content[6:]
            msg += "\n\nReact with :regional_indicator_d: for DM, "
            msg += ":regional_indicator_h: for Hunter, "
            msg += "or :regional_indicator_f: for Fugitive."
            msg = await client.get_channel(783883079449313280).send(msg)
            
            emojis = ['\U0001F1E9', '\U0001F1EB', '\U0001F1ED'] #[D, F, H]
            for emoji in emojis: await msg.add_reaction(emoji)
            return

        #status change
        if content[0] == "/status":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            await message.channel.send("Status changed")
            return

        #location change
        if content[0] == "/setloc":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            if len(content) < 3:
                await message.channel.send("Invalid syntax")
                return
            await message.delete()
            await client.get_channel(785015450182090783).send(place(content[1], content[2])[0])
            return
            
        if content[0] == "/move":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            if len(content) < 3:
                await message.channel.send("Invalid syntax")
                return
            await message.delete()
            await client.get_channel(785015450182090783).send(move(content[1], content[2])[0])
            return
            
        if content[0] == "/find":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            if len(content) < 2:
                await message.channel.send("Invalid syntax")
                return
            await message.delete()
            await client.get_channel(785018915469656064).send(find(content[1])[0])
            return
            
        if content[0] == "/check":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await channel.send("You don't have permission to send this command.")
                return
            if len(content) < 2:
                await message.channel.send("Invalid syntax")
                return
            await message.delete()
            await client.get_channel(785018915469656064).send(check(content[1])[0])
            return
            
        if content[0] == "/kill":
            for role in message.author.roles:
                if role.id == 783900332757352458: break #DM
                if role.id == 784262916844683285: break #Tech Support
            else:
                await message.channel.send("You don't have permission to send this command.")
                return
            if len(content) < 2:
                await message.channel.send("Invalid syntax")
                return
            await message.delete()
            await client.get_channel(785015450182090783).send(remove(content[1])[0])
            return

#announcements
    if message.channel.name == "public-announcements":
        await message.delete()
        temp = await message.channel.send(f"{message.content}")
        return

#testing
    if message.channel.name == "query-response":
        #await message.delete()
        #await message.channel.send(f"{message.channel.id}")
        return


client.run(TOKEN)

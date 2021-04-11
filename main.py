import replit
from replit import db
import discord
from discord.ext import commands
import os
from WebServer import start_server
import numpy as np


def get_id(obj, id_type: str) -> str:
	if obj is not str: obj = str(obj.id)
	if id_type == "guild": text = ""
	elif id_type == "role": text = "@&"
	elif id_type == "channel": text = "#"
	return '<' + text + obj + '>'


def make_guild(id: str) -> str:
	db[id] = {
		"command_char": '/',
		"role": {"gm":None, "hunter":None, "fugitive":None, "citizen":None, "alt admin":None},
		"channel": {
			"global announcements":None, "game announcements":None,
			"hunter announcements":None, "fugitive announcements":None,
			"global bot commands":None, "gm bot commands":None,
			"hunter bot commands":None, "fugitive bot commands":None,
			"global map": None, "gm map":None, "hunter map":None, "fugitive map":None},
		"emoji": {"gm":None, "hunter":None, "fugitive":None}}
	return f"\nGuild '{id}' added to database"

def set_guild(guild: discord.Guild, data: dict) -> str:
	id = str(guild.id)
	text = ""
	if not (id in db.keys()): text = make_guild(id) # Add guild to database if missing

	for key in data: # For each key in data
		try: # If key isn't already defined: ignore
			# If data is complex (dict)
			if type(data[key]) is dict and type(db[id][key]) is replit.database.database.ObservedDict:
				for subkey in db[id][key]:
					try:
						db[id][key][subkey] = data[key][subkey] # Set db value
						text = text + f"\n'{subkey}' set to {data[key][subkey]}"
					except KeyError:
						text = text + f"\n'{subkey}' remains as {db[id][key][subkey]}"

			elif type(data[key]) is type(db[id][key]): # If data is simple (str)
				db[id][key] = data[key] # Set db value
			#text = text + f"\n'{key}' set for guild '{guild.name}'"
		except KeyError: # If key isn't valid for database, it will throw a KeyError
			text = text + f"\n'{key}' is not valid"
	return text


guild_emojis = {
	"GM":       '\U0001F1E9',
	"Hunter":   '\U0001F1ED',
	"Fugitive": '\U0001F1EB',
	"Player":   0}



async def get_prefix(bot, message):
	if message.guild and (str(message.guild.id) in db):
		return db[str(message.guild.id)]["command_char"] 
	return '/'
bot = commands.Bot(command_prefix=get_prefix, case_insensitive=True)

@bot.event
async def on_ready(): 
	print(f"\n\nLogged in as: {bot.user}")



def is_admin():
	def predicate(ctx):
		if ctx.guild:
			if ctx.author.guild_permissions.administrator: return True
			id = str(ctx.guild.id)
			if id in db:
				for role in ctx.author.roles:
					role_id = get_id(role, "role")
					if role_id == db[id]["role"]["gm"]: return True
					if role_id == db[id]["role"]["alt admin"]: return True
		return False
	return commands.check(predicate)

# def is_gm():
# 	def predicate(ctx):
# 		if ctx.guild:
# 			id = str(ctx.guild.id)
# 			if id in db:
# 				for role in ctx.author.roles:
# 					if role.id == db[id]["role"]["gm"]: return True
# 		return False
# 	return commands.check(predicate)
# def is_tech():
# 	def predicate(ctx):
# 		if ctx.guild:
# 			id = str(ctx.guild.id)
# 			if id in db:
# 				for role in ctx.author.roles:
# 					if role.id == db[id]["role"]["alt_admin"]: return True
# 		return False
# 	return commands.check(predicate)


@bot.command()
@is_admin()
async def announce(ctx, *, text):
	await ctx.send(text)

@bot.command()
@is_admin()
async def repeat(ctx, *, text):
	await ctx.send(text)
	await ctx.message.delete()

@bot.command()
@is_admin()
async def set(ctx, key: str, guild_link, *, subkey: str):
	if ctx.guild:
		if key == "role": link = await commands.RoleConverter().convert(ctx, guild_link)
		elif key == "channel": link = await commands.TextChannelConverter().convert(ctx, guild_link)
		await ctx.send( set_guild(ctx.guild, {key: {subkey: get_id(link, key)}}) )

@bot.command()
@is_admin()
async def check(ctx, key: str):
	if ctx.guild:
		await ctx.send( set_guild(ctx.guild, {key: {'_': '_'}}) )

@bot.command()
async def roll(ctx, *args):
	if len(args)==0 or args[0].lower()=="stats":
		text = "Your stat roll is:"
		rolls = np.sort(np.floor(np.random.rand(6,4)*6 + 1).astype(int))
		stats = np.sum(rolls, axis=1, where=[0,1,1,1])
		for i in reversed(np.argsort(stats)):
			text = text + "\n`" + str(rolls[i][::-1]) + " = " + str(stats[i]) + "`"
		await ctx.send(text)



start_server()
bot.run(os.getenv('TOKEN'))
import replit
from replit import db
import discord
from discord.ext import commands
import os
from WebServer import start_server
import numpy as np


async def get_prefix(bot, message):
	if message.guild:
		id = get_id(message.guild)
		if id in db:
			return db[id]["command character"] 
	return '/'
bot = commands.Bot(command_prefix=get_prefix, case_insensitive=True)
@bot.event
async def on_ready(): 
	print(f"\n\nLogged in as: {bot.user}")



def get_id(obj, id_type: str = "") -> str:
	if   isinstance(obj, discord.role.Role):
		id_type = "role"
		obj = str(obj.id)
	elif isinstance(obj, discord.channel.TextChannel):
		id_type = "channel"
		obj = str(obj.id)
	elif isinstance(obj, discord.guild.Guild):
		id_type = "guild"
		obj = str(obj.id)
	elif isinstance(obj, discord.ext.commands.context.Context):
		id_type = "guild"
		obj = str(obj.guild.id)
	elif isinstance(obj, int): obj = str(obj)
	else: id_type = "emoji"

	if id_type == "role":
		return f"<@&{obj}>"
	elif id_type == "channel":
		return f"<#{obj}>"
	return obj

def get_command_char(id: str) -> str:
	return db[id]["command character"]

def set_subkey(id: str, key: str, subkey: str, value: str = None) -> str:
	spaces = " "*(db["max subkey length"][key]-len(subkey))
	text = f"`{spaces}{subkey}`"

	if value == None:
		return f"{text} : {db[id][key][subkey]}"
	elif db[id][key][subkey] == value:
		return f"{text} remain: {value}"
	else:
		db[id][key][subkey] = value
		return f"{text} set to: {value}"
def print_subkey(id: str, key: str, subkey: str) -> str:
	return set_subkey(id, key, subkey)

def set_key(id: str, key: str, sub_vals: dict = None) -> str:
	text = f"___{key}___ settings on server: ___{id}___"
	if key == "command character": return f"{text}\n" + f"`{key}` : `{db[id][key]}`"
	for subkey in db[id][key]:
		if isinstance(sub_vals, dict) and (subkey in sub_vals):
			value = sub_vals[subkey]
		else: value = None
		text = f"{text}\n" + f"{set_subkey(id, key, subkey, value)}"
	return text
def print_key(id: str, key: str) -> str:
	return set_key(id, key)

def add_guild(id: str) -> str:
	if id in db.keys(): return f"Guild ___{id}___ already exists in database"
	db[id] = db["default"]
	return f"Guild ___{id}___ added to database"
def add_key(key: str, subkeys: dict = None, value: str = None) -> str:
	if key in db["default"]: return f"Key ___{key}___ already exists in database"
	if subkeys is None:
		db["default"][key] = value
		return f"Key ___{key}___ set to `{value}`"
	if isinstance(subkeys, dict):
		db["default"][key] = dict()
		text = f"___{key}___ added to default server:"
		text = text + add_subkeys("default", key, subkeys) + "\nAlso added to servers:"
		for id in db.keys():
			if id == "max subkey length": continue
			if id == "default": continue
			db[id][key] = db["dafault"][key]
			text = text + f" ___{id}___"
		return text
def add_subkeys(id: str, key: str, subkeys: dict) -> str:
	text = ""
	for subkey in subkeys:
		text = text + "\n" + add_subkey(id, key, subkey, subkeys[subkey])
	return text
def add_subkey(id: str, key: str, subkey: str, value: str = None) -> str:
	if subkey in db[id][key]: return set_subkey(id, key, subkey, value)
	db[id][key][subkey] = value
	spaces = " "*(db["max subkey length"][key]-len(subkey))
	return f"`{spaces}{subkey}` add as: {value}"

def set_guild(guild: discord.Guild, data: dict) -> str:
	id = get_id(guild)
	text = ""
	if not (id in db.keys()): text = add_guild(id) # Add guild to database if missing

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


def is_admin():
	def predicate(ctx):
		if ctx.author.guild_permissions.administrator: return True
		id = get_id(ctx)
		if id in db:
			for role in ctx.author.roles:
				role_id = get_id(role)
				if role_id == db[id]["role"]["gm"]: return True
				if role_id == db[id]["role"]["alt admin"]: return True
		return False
	return commands.check(predicate)


@bot.command()
@commands.guild_only()
@is_admin()
async def announce(ctx, *, text):
	await ctx.send(text)

@bot.command()
@commands.guild_only()
@is_admin()
async def repeat(ctx, *, text):
	await ctx.send(text)
	await ctx.message.delete()

@bot.command()
@commands.guild_only()
@is_admin()
async def set(ctx, key: str, guild_link, *, subkey: str):
	if key == "role": guild_link = await commands.RoleConverter().convert(ctx, guild_link)
	elif key == "channel": guild_link = await commands.TextChannelConverter().convert(ctx, guild_link)
	elif key == "emoji": pass
	else:
		await ctx.send(f"{key} is an invalid key.")
		return
	await ctx.send(set_key( get_id(ctx), key, {subkey: get_id(guild_link)} ))

@bot.command()
@commands.guild_only()
async def check(ctx, key: str):
	id = get_id(ctx)
	key = key.lower()
	if key.startswith("command"):
		await ctx.send(print_key(id, "command character"))

	elif key.startswith("role"):
		await ctx.send(print_key(id, "role"))

	elif key.startswith("channel"):
		await ctx.send(print_key(id, "channel"))

	elif key.startswith("emoji"):
		await ctx.send(print_key(id, "emoji"))

	elif key == "all":
		text = f"Displaying all settings for server: ___{id}___..."
		for key in db[id]: text = f"{text}\n" + print_key(id, key)
		await ctx.send(text)

	else: await ctx.send( "Unknown key." )



def roll_dice(die, count):
	rng = np.random.default_rng()
	return rng.integers(1, die, count, endpoint=True)
@bot.command(aliases=['r'])
async def roll(ctx, *args):
	if len(args) == 0: args = ["1d20"]

	for i in range(len(args)):
		arg = args[i].lower()
		if arg == "stats":
			count, die, drop, repeat = 4, 6, 1, 6
			text = "Your stat roll is:"

			rolls = roll_dice(die, (repeat, count))
			order = np.argsort(rolls, axis=1)
			drops = np.ones((repeat, count), bool)
			for i in range(repeat):
				for j in order[i][:drop]:
					drops[i][j] = 0
			stats = np.sum(rolls, axis=1, where=drops)
			for i in range(len(stats)):
				text = text + "\n`" + str(rolls[i][::-1]) + " = " + str(stats[i]) + "`"
			await ctx.send(text)
		
		else:
			repeat = 1
			arg = arg.split('d')
			count = int(arg[0])
			arg = arg[1].split('k')
			die = int(arg[0])
			if len(arg)<2: drop = 0
			else: drop = arg[1]

			text = "Your roll is:"
			rolls = roll_dice(die, (repeat, count))
			order = np.argsort(rolls, axis=1)
			drops = np.ones((repeat, count), bool)
			for i in range(repeat):
				for j in order[i][:drop]:
					drops[i][j] = 0
			stats = np.sum(rolls, axis=1, where=drops)
			for i in range(len(stats)):
				text = text + "\n`" + str(rolls[i][::-1]) + " = " + str(stats[i]) + "`"
			await ctx.send(text)


# @bot.event
# async def on_command_error(ctx, error):
# 	await ctx.send(error)

start_server()
bot.run(os.environ['TOKEN'])

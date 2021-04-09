from replit import db
import discord
from discord.ext import commands
import os
from WebServer import start_server


def add_guild(guild: discord.Guild) -> str:
	id = str(guild.id)
	if id in db.keys(): return f"\nGuild '{guild.name}' already exists in database"
	#else
	db[id] = {
		"command_char": '/',
		"roles": {"gm":None, "hunter":None, "fugitive":None, "citizen":None, "alt_admin":None},
		"channels": {
			"global announcements":None, "game announcements":None,
			"hunter announcements":None, "fugitive announcements":None,
			"global bot commands":None, "gm bot commands":None,
			"hunter bot commands":None, "fugitive bot commands":None,
			"global map": None, "gm map":None, "hunter map":None, "fugitive map":None},
		"emojis": {"gm":None, "hunter":None, "fugitive":None}}
	return f"\nGuild '{guild.name}' added to database"

def set_guild(guild: discord.Guild, data: dict) -> str:
	id = str(guild.id)
	text = ""
	if not (id in db.keys()): text = add_guild(guild) # Add guild to database if missing

	for key in data: # For each key in data
		try: # If key isn't already defined: ignore
			if type(data[key]) is type(db[id][key]): # If data is simple (str)
				db[id][key] = data[key] # Set db value
			elif type(data[key]) is dict: # If data is complex (dict)
				for subkey in db[id][key]:
					try:
						db[id][key][subkey] = data[key][subkey] # Set db value
						text = text + f"\n'{subkey}' set to '{data[key][subkey]}'"
					except KeyError:
						text = text + f"\n'{subkey}' remains as '{db[id][key][subkey]}'"
			text = text + f"\n'{key}' set for guild '{guild.name}'"
		except KeyError: # If key isn't valid for database, it will throw a KeyError
			text = text + f"\n'{key}' is not valid"
	return text



guild_commands = ('/', '!')
guild_channels = {
	"Announcements": 783883079449313280,
	"Map":           785015450182090783,
	"Responses":     785018915469656064}
guild_roles = {
	"GM":       783900332757352458,
	"Hunter":   783900808039104572,
	"Fugitive": 783900625544413214,
	"Player":   784287952012050452,
	"Citizen":  783900909578747944,
	"Admin":    784262916844683285}
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
			if ctx.author.guild_permissions(administrator=True): return True
			id = str(ctx.guild.id)
			if id in db:
				for role in ctx.author.roles:
					if role.id == db[id]["roles"]["gm"]: return True
					if role.id == db[id]["roles"]["alt_admin"]: return True
		return False
	return commands.check(predicate)

def is_gm():
	def predicate(ctx):
		if ctx.guild:
			id = str(ctx.guild.id)
			if id in db:
				for role in ctx.author.roles:
					if role.id == db[id]["roles"]["gm"]: return True
		return False
	return commands.check(predicate)
def is_tech():
	def predicate(ctx):
		if ctx.guild:
			id = str(ctx.guild.id)
			if id in db:
				for role in ctx.author.roles:
					if role.id == db[id]["roles"]["alt_admin"]: return True
		return False
	return commands.check(predicate)


@bot.command()
@is_admin()
async def announce(ctx, *, text):
	await ctx.send(text)

@bot.command()
@is_admin()
#@commands.check_any(is_gm(), is_tech(), commands.has_guild_permissions(administrator=True))
async def repeat(ctx, *, text):
	await ctx.send(text)
	await ctx.message.delete()

@bot.command()
@commands.check_any(is_gm(), is_tech(), commands.has_guild_permissions(administrator=True))
async def setRole(ctx, guild_role: discord.Role, role_type: str):
	if ctx.guild:
		await ctx.send( set_guild(ctx.guild, {"roles": {role_type: guild_role.id}}) )

start_server()
bot.run(os.getenv('TOKEN'))
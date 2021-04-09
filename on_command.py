


async def on_command(message, client, channels, roles, emojis):
	contents = message.content[1:].split(' ')

	#help
	if contents[0] == "help":
		await message.delete()
		await message.channel.send(help())
		return

	#start
	if contents[0] == "start":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		
		msg = await client.get_channel(channels["Announcements"]).history(limit=1).flatten()
		await msg[0].add_reaction('✅')
		
		await message.delete()
		await client.get_channel(channels["Announcements"]).send(
				"Roles have been assigned. Let the game commence!") #public-announcements
		return

	#announce
	if contents[0] == "announce":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		
		await message.delete()
		await client.get_channel(channels["Announcements"]).send(message.contents[9:])
		return

	#begin
	if contents[0] == "begin":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		
		await message.delete()
		msg = message.contents[6:]
		msg += "\n\nReact with :regional_indicator_d: for DM, "
		msg += ":regional_indicator_h: for Hunter, "
		msg += "or :regional_indicator_f: for Fugitive."
		msg = await client.get_channel(channels["Announcements"]).send(msg)
		
		for emoji in ("GM", "Hunter", "Fugitive"): await msg.add_reaction(emojis[emoji]) #[D, H, F]
		return

	#status change
	if contents[0] == "status":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		await message.channel.send("Status changed")
		return

	#location change
	if contents[0] == "setloc":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		if len(contents) < 3:
			await message.channel.send("Invalid syntax")
			return
		await message.delete()
		await client.get_channel(channels["Map"]).send(place(contents[1], contents[2])[0])
		return
	
	#move
	if contents[0] == "move":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		if len(contents) < 3:
			await message.channel.send("Invalid syntax")
			return
		await message.delete()
		await client.get_channel(channels["Map"]).send(move(contents[1], contents[2])[0])
		return
	
	#find
	if contents[0] == "find":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		if len(contents) < 2:
			await message.channel.send("Invalid syntax")
			return
		await message.delete()
		await client.get_channel(channels["Responses"]).send(find(contents[1])[0])
		return
	
	#check
	if contents[0] == "check":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		if len(contents) < 2:
			await message.channel.send("Invalid syntax")
			return
		await message.delete()
		await client.get_channel(channels["Responses"]).send(check(contents[1])[0])
		return
	
	#kill
	if contents[0] == "kill":
		for role in message.author.roles:
			if role.id == roles["GM"]: break #DM
			if role.id == roles["Admin"]: break #Tech Support
		else:
			await message.channel.send("You don't have permission to send this command.")
			return
		if len(contents) < 2:
			await message.channel.send("Invalid syntax")
			return
		await message.delete()
		await client.get_channel(channels["Map"]).send(remove(contents[1])[0])
		return

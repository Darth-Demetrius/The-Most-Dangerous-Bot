def help_text(c = '/'):
	c = "\n" + c

	text = []
	#help
	text.append(f"{c}help: Displays this list")
	#announce
	text.append(f"{c}announce [message]: Sends the message to the public-announcements channel")
	#begin
	text.append(f"{c}begin [message]: As /announce but adds reactions for role claiming")
	#text.append("\n - choose availible roles")
	#start
	text.append(f"{c}start: Sends message announcing the start of the game")
	text.append(" - Assigns roles based on reactions to most recent post in public-announcements")
	text.append(" - Only one game may be running at a time")
	#setloc
	text.append(f"{c}setloc [Player] [Location]: Creates a new player and sets him on the map")
	text.append(" - [Player]: The name or representation of the player - cannot contain any whitespace")
	text.append(" - [Location]: Where to set the player - must be in the format [Letter][Number]")
	text.append(" - Returns: '[Player] moved to [Location]' if successful")
	#move
	text.append(f"{c}move [Player] [Location]: Moves a player to a different location on the map")
	text.append(" - [Player]: The name or representation of the player")
	text.append(" - [Location]: Where to move the player - must be in the format [Letter][Number]")
	text.append(" - Returns: '[Player] moved to [Location]' if successful")
	#find
	text.append(f"{c}find [Player]: Find the location of a player on the map")
	text.append(" - [Player]: The name or representation of the player")
	text.append(" - Returns: [Player] is at [grid location]")
	#check
	text.append(f"{c}check [Location]: Checks a location on the map for players")
	text.append(" - [Location]: The location to check - must be in the format [Letter][Number]")
	text.append(" - Returns: 'Grid location: Player1 Player2 ...' if successful")
	#kill
	text.append(f"{c}kill [Player]: Removes a player from anywhere on the map")
	text.append(" - [Player]: The name or representation of the player")
	text.append(" - Returns: '[Player] killed' if successful")

	#disclaimer
	text.append("\nCurrently there is no persistant memory, each time the bot is upgraded or restarted, the saved locations are lost.")
	
	text = "\n".join(text)
	return text
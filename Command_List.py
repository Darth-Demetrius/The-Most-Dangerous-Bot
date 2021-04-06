def help():
	text = []
	#help
	text.append("\n/help: Displays this list")
	#announce
	text.append("\n\n/announce: Sends this message (minus the command) to the public-announcements channel")
	#begin
	text.append("\n\n/begin: As /announce but adds reactions for role claiming")
	#text.append("\n - choose availible roles")
	#start
	text.append("\n\n/start: Sends message announcing the start of the game")
	text.append("\n - Assigns roles based on reactions to most recent post in public-announcements")
	text.append("\n - Only one game may be running at a time")
	#setloc
	text.append("\n\n/setloc {Player} {Location}: Creates a new player and sets him on the map")
	text.append("\n - {Player}: The name or representation of the player - cannot contain any whitespace")
	text.append("\n - {Location}: Where to set the player - must be in the format {Letter}{Number}")
	text.append("\n - Returns: '{Player} moved to {Location}' if successful")
	#move
	text.append("\n\n/move {Player} {Location}: Moves a player to a different location on the map")
	text.append("\n - {Player}: The name or representation of the player")
	text.append("\n - {Location}: Where to move the player - must be in the format {Letter}{Number}")
	text.append("\n - Returns: '{Player} moved to {Location}' if successful")
	#find
	text.append("\n\n/find {Player}: Find the location of a player on the map")
	text.append("\n - {Player}: The name or representation of the player")
	text.append("\n - Returns: {Player} is at {grid location}")
	#check
	text.append("\n\n/check {Location}: Checks a location on the map for players")
	text.append("\n - {Location}: The location to check - must be in the format {Letter}{Number}")
	text.append("\n - Returns: 'Grid location: Player1 Player2 ...' if successful")
	#kill
	text.append("\n\n/kill {Player}: Removes a player from anywhere on the map")
	text.append("\n - {Player}: The name or representation of the player")
	text.append("\n - Returns: '{Player} killed' if successful")

	#disclaimer
	text.append("\n\nCurrently there is no persistant memory, each time the bot is upgraded or restarted, the saved locations are lost.")
	
	text = "".join(text)
	return text
#location data

map =  [[[],[],[],[],[],[],[],[],[]],
		[[],[],[],[],[],[],[],[],[]],
		[[],[],[],[],[],[],[],[],[]],
		[[],[],[],[],[],[],[],[],[]],
		[[],[],[],[],[],[],[],[],[]],
		[[],[],[],[],[],[],[],[],[]],
		[[],[],[],[],[],[],[],[],[]]]


def index(player):
	for r,row in enumerate(map):
		for c,squ in enumerate(row):
			for pla in squ:
				if pla == player: return [[r, c], 0]
	return [f"{player} not found", 1]

def find(player):
	loc = index(player)
	if loc[1] == 1: return loc
	return(f"{player} is at {chr(loc[0][0]+0x41)}{loc[0][1]+1}", 0)

def place(player, location):
	if find(player)[1] == 0: return ["Player already in game", 1]
	try:
		if len(location) == 2:
			row = ord(location[0].upper())-0x41
			if row < 0 or row > 6: raise x
			col = int(location[1])-1
			if col < 0 or col > 8: raise x
		else: raise x
		map[row][col].append(player)
	except: return ["Invalid location", 1]
	return [f"{player} moved to {location}", 0]

def remove(player):
	loc = index(player)
	if loc[1] == 1: return loc
	map[loc[0][0]][loc[0][1]].remove(player)
	return [f"{player} killed", 0]

def move(player, location):
	temp = remove(player)
	if temp[1] == 1: return temp
	return place(player, location)

def check(location):
	try:
		if len(location) == 2:
			row = ord(location[0].upper())-0x41
			if row < 0 or row > 6: raise x
			col = int(location[1])-1
			if col < 0 or col > 8: raise x
		else: raise x
	except: return [f"{location} is an invalid location", 1]
	temp = location + ": "
	for player in map[row][col]: temp += " " + player
	return [temp, 0]

#print(place("Berg", "A1"))
#print(place("Loogie", "B5"))
#print(place("Berg", "G4"))

#print(find("Berg"))
#print(find("Loogies"))

#print(move("Berg", "B5"))
#print(place("Berge", "G0"))

#print(find("Berg"))
#print(check("A1"))
#print(check("B5"))
#input("-")
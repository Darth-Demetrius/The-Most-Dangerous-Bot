"""User-facing help and instruction text for the REPL cogs."""

REPL_CODING_INSTRUCTIONS = r"""
To execute a block of code, send a message containing a triple-backtick code block labeled `python` or `py` after the opening fences. For example:
> \`\`\`pythn
> x=5
> print(x\*\*2)
> \`\`\`
You can also use single backticks for short one-liners, e.g. `` `5**3` ``.
If the code produces output, it will be sent back as a message. If there is no output, a ✅ reaction will be added to your message. If there is an error during execution, the error message will be sent back.
If the code generates matplotlib figures, image outputs will be attached to the response.
To label one code block for traceback output, add `input_name=...` (or shorthand `i=...`) to the opening fence. For example:
> \`\`\`python input_name="initiative {count}"
> total = 1 / 0
> \`\`\`

math, random, and MyDyce are imported by default, and you can import other modules as needed (subject to your permission level). Your session state will persist in-memory as long as the bot is running, and you can optionally save it to the database when closing the session to restore later.
d4, d6, d8, d10, d12, d20, and d100 are initialized by default as dyce.H(sides) objects for convenient use. Use examples:
> \`\`\`py
> (2@d6).roll()  # roll and sum 2d6
> (2@P(d6)).roll()  # roll 2d6 (or `P(d6,d6).roll()`)
> print((d6-d4).format())  # show the distribution for 1d6 minus 1d4
> (d20+5).mean()  # expected value of a d20 roll plus 5
>
> h_4d6_k3 = (4@P(d6)).h(-1,-2,-3)  # define 4d6k3
> print(h_4d6_k3.format())  # show the distribution for 4d6k3
> stat_block = 6@P(h_4d6_k3)  # create a D&D 5e stat block of 6 4d6k3 rolls
> sorted(stat_block.roll())  # roll a standard array
> print(stat_block.h(0).format())  # distribution for lowest stat in the block
> print(stat_block.h(-1).format())  # distribution for highest stat in the block
> \`\`\`

"""

REPL_HELP_LINES = [
    "/repl open - Open a Python REPL session.",
    "/repl instructions - Show REPL coding instructions.",
    "/repl close - Close your current REPL session.",
    "/repl save - Save your current active REPL session.",
    "/repl delete - Delete your saved REPL session.",
    "/repl status - Show your active and saved REPL session state.",
    "/repl variables - List variables from your active session, saved session, or both.",
    "/repl permissions - Show your effective REPL permission level.",
    "/repl imports - Show imports enabled now or allowed by policy.",
    "/repl input_name - Set or reset the default traceback input-name template.",
]

REPL_ADMIN_HELP_LINES = [
    "/repl_admin set_permissions - Set REPL permissions for a guild role (bot owner only).",
    "/repl_admin list_permissions - List stored REPL permissions for the current guild or DM scope.",
    "/repl_admin delete_permissions - Delete stored REPL permissions for the current guild.",
    "/repl_admin saved_sessions - List saved REPL sessions for the current scope.",
    "/repl_admin purge_session - Delete a saved REPL session for a user in the current scope (owner only).",
]

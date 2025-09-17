# bot.py
import os
import discord
from discord import app_commands
from datetime import datetime
import sheets_client as sheets
import asyncio

BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = 1417511115734388887
GUILD_ID   = 627647267414999065


class MyClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild)

client = MyClient()

def normalize_date(user_input: str) -> str:
    """
    Accepts:
      - d.m
      - dd.m
      - d.mm
      - d.m.yyyy
    Separators: '.', '/', '-'
    If year is missing, uses current year.
    Returns normalized 'dd.mm.yyyy' or raises ValueError.
    """
    if not user_input or not isinstance(user_input, str):
        raise ValueError("empty date")

    # normalize separators and strip spaces
    s = user_input.strip().replace("/", ".").replace("-", ".")
    parts = [p for p in s.split(".") if p != ""]
    if len(parts) not in (2, 3):
        raise ValueError("use d.m or d.m.yyyy")

    today = datetime.today()
    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else today.year
    except ValueError:
        raise ValueError("numbers only in date")

    # validate by constructing datetime (this catches impossible dates)
    try:
        dt = datetime(year, month, day)
    except ValueError:
        raise ValueError("invalid calendar date")

    return dt.strftime("%d.%m.%Y")

def _in_right_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel_id == CHANNEL_ID

def _valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
        return True
    except ValueError:
        return False

@client.tree.command(
    name="cant",
    description="Put a ✖ on a date (e.g., 7.9 or 7.9.2025).",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(date="Date like 7.9 or 7.9.2025 (year optional)")
async def cant(interaction: discord.Interaction, date: str):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in appointments please :/", ephemeral=True)
        return

    # respond fast to avoid 'Unknown interaction'
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        norm = normalize_date(date)  # -> 'dd.mm.yyyy'
    except ValueError as e:
        await interaction.followup.send(f"Invalid date: {e}. Examples: 7.9  or  07.09.2025")
        return

    ok = await asyncio.to_thread(sheets.set_raid_date_in_visible_table, norm, False)
    if ok:
        await interaction.followup.send(f"Saved: **{norm}** → ✖")
    else:
        await interaction.followup.send("Date not found in the current 3-month range.")

@client.tree.command(
    name="can",
    description="Put a ✔ on a date (e.g., 7.9 or 7.9.2025).",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(date="Date like 7.9 or 7.9.2025 (year optional)")
async def can_cmd(interaction: discord.Interaction, date: str):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in appointments please :/", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        norm = normalize_date(date)
    except ValueError as e:
        await interaction.followup.send(f"Invalid date: {e}. Examples: 7.9  or  07.09.2025")
        return

    ok = await asyncio.to_thread(sheets.set_raid_date_in_visible_table, norm, True)
    if ok:
        await interaction.followup.send(f"Saved: **{norm}** → ✔")
    else:
        await interaction.followup.send("Date not found in the current 3-month range.")
@client.tree.command(
    name="rebuild",
    description="Rebuild the 3-month schedule (defaults: Mon/Wed/Thu = ✔).",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(start_from_day1="Start current month at day 1 instead of today (default: false)")
async def rebuild_cmd(interaction: discord.Interaction, start_from_day1: bool = False):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in appointments please :/", ephemeral=True)
        return

    # Acknowledge within 3s to avoid 10062 Unknown interaction
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        # Run blocking Google API calls off the event loop
        await asyncio.to_thread(
            sheets.rebuild_schedule,
            start_current_from_today=not start_from_day1
        )
        await interaction.followup.send("Schedule rebuilt.")
    except Exception as e:
        await interaction.followup.send(f"Rebuild failed: `{e}`")
client.run(BOT_TOKEN)

@client.tree.command(name="refresh", description="Refresh sheet (preserves ✔/✖ overrides).",
                     guild=discord.Object(id=GUILD_ID))
async def refresh_cmd(interaction: discord.Interaction):
    # optional: restrict to your bot channel
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Use this in the appointments channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        import sheets_client as sheets
        sheets.refresh_schedule_preserve_overrides()
        await interaction.followup.send("✅ Schedule refreshed (overrides preserved).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Refresh failed: `{e}`", ephemeral=True)



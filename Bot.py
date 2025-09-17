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

# /cant — add name, force ✖
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
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        norm = normalize_date(date)
    except ValueError as e:
        await interaction.followup.send(f"Invalid date: {e}. Examples: 7.9  or  07.09.2025")
        return

    # prefer server display name
    user_name = interaction.user.display_name or interaction.user.name
    found, names = await asyncio.to_thread(sheets.add_cant_user, norm, user_name)
    if found:
        await interaction.followup.send(f"Saved: **{norm}** → ✖  (can't: {names})")
    else:
        await interaction.followup.send("Date not found in the current 3-month range.")

# /can — remove name; if none left → ✔, else keep ✖
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

    user_name = interaction.user.display_name or interaction.user.name
    found, new_flag, names = await asyncio.to_thread(sheets.remove_cant_user, norm, user_name)
    if found:
        if names:
            await interaction.followup.send(f"Updated: **{norm}** → {new_flag}  (can't: {names})")
        else:
            await interaction.followup.send(f"Updated: **{norm}** → ✔  (nobody marked as can't)")
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
from discord.ext import tasks

# /remind on [time]
@client.tree.command(
    name="remind_on",
    description="Enable raid-day reminders (✔ days). Optional time HH:MM (server time).",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(time="HH:MM (24h). Default 17:00")
async def remind_on(interaction: discord.Interaction, time: str | None = None):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in appointments please :/", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    hhmm = (time or "17:00").strip()
    # naive sanity
    try:
        h,m = map(int, hhmm.split(":"))
        assert 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        await interaction.followup.send("Time must be HH:MM (24-hour), e.g. 17:00", ephemeral=True)
        return

    await asyncio.to_thread(
        sheets.set_reminder,
        interaction.user.id,
        f"{interaction.user.name}#{interaction.user.discriminator}" if hasattr(interaction.user,"discriminator") else interaction.user.name,
        True,
        hhmm
    )
    await interaction.followup.send(f"✅ Reminders enabled at **{hhmm}** on ✔ days.", ephemeral=True)

# /remind off
@client.tree.command(
    name="remind_off",
    description="Disable raid-day reminders.",
    guild=discord.Object(id=GUILD_ID)
)
async def remind_off(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in appointments please :/", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    await asyncio.to_thread(
        sheets.set_reminder,
        interaction.user.id,
        f"{interaction.user.name}#{interaction.user.discriminator}" if hasattr(interaction.user,"discriminator") else interaction.user.name,
        False
    )
    await interaction.followup.send("🛑 Reminders disabled.", ephemeral=True)

def _now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")  # server time

@tasks.loop(minutes=1)
async def reminder_loop():
    try:
        # Fast exit if today isn’t a raid day
        if not await asyncio.to_thread(sheets.is_today_raid_day):
            return

        reminders = await asyncio.to_thread(sheets.get_enabled_reminders)
        if not reminders:
            return

        now_hhmm = _now_hhmm()
        today_iso = datetime.today().strftime("%Y-%m-%d")
        to_ping = [r for r in reminders if (r["time"] == now_hhmm and r.get("last","") != today_iso)]

        if not to_ping:
            return

        # Build mentions and send once
        channel = client.get_channel(CHANNEL_ID)
        if channel is None:
            return

        mentions = " ".join(f"<@{r['user_id']}>" for r in to_ping)
        await channel.send(f"⏰ Raid reminder (✔ today)! {mentions}")

        # Mark notified
        for r in to_ping:
            await asyncio.to_thread(sheets.mark_notified, r["user_id"], today_iso)

    except Exception as e:
        # Optional: log to console
        print(f"[reminder_loop] error: {e}")

@reminder_loop.before_loop
async def _wait_until_ready():
    await client.wait_until_ready()


reminder_loop.start()
client.run(BOT_TOKEN)




# bot.py
import os
import discord
from discord import app_commands
from discord.ext import tasks        
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import sheets_client as sheets
import asyncio


BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = 1417511115734388887
GUILD_ID   = 627647267414999065
PLANNED_DAYS = {0, 2, 3}
BERLIN = ZoneInfo("Europe/Berlin")  # du nutzt Berlin bereits

class MyClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild)

        # Start background reminder loop AFTER the bot is ready/loop exists
        if not reminder_loop.is_running():
            reminder_loop.start()
            
        if not daily_refresh_loop.is_running():
            daily_refresh_loop.start()

        if not next7_dashboard_loop.is_running():
            next7_dashboard_loop.start()

        # optional: sofort beim Start aktualisieren
        ch = self.get_channel(CHANNEL_ID)
        if ch:
            await _upsert_dashboard_message(ch)

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
        await interaction.response.send_message("Only in schedule-commands please :/", ephemeral=True)
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
        await interaction.response.send_message("Only in schedule-commands please :/", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        norm = normalize_date(date)
    except ValueError as e:
        await interaction.followup.send(f"Invalid date: {e}. Examples: 7.9  or  07.09.2025")
        return
        
# NEW: only allow ✔ on planned raid days (Mon/Wed/Thu)
    dt = datetime.strptime(norm, "%d.%m.%Y")
    if dt.weekday() not in PLANNED_DAYS:
        await interaction.followup.send(
            "That date is **not a planned raid day** (Mon/Wed/Thu) – it stays **✖**.",
            ephemeral=True
        )
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

@client.tree.command(name="refresh", description="Refresh sheet (preserves ✔/✖ overrides).",
                     guild=discord.Object(id=GUILD_ID))
async def refresh_cmd(interaction: discord.Interaction):
    # optional: restrict to your bot channel
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in schedule-commands please :/", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        sheets.refresh_schedule_preserve_overrides()
        await interaction.followup.send("✅ Schedule refreshed (overrides preserved).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Refresh failed: `{e}`", ephemeral=True)

# /remind on [time]
@client.tree.command(
    name="remind_on",
    description="Enable raid reminder. Optional time HH:MM (Selected timezone otherwise server time).",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(time="HH:MM (24h). Default 17:00")
async def remind_on(interaction: discord.Interaction, time: str | None = None):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in schedule-commands please :/", ephemeral=True)
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
    await interaction.followup.send(f"✅ Reminders enabled at **{hhmm}** on raid days.", ephemeral=True)

# /remind off
@client.tree.command(
    name="remind_off",
    description="Disable raid reminders.",
    guild=discord.Object(id=GUILD_ID)
)
async def remind_off(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in schedule-commands please :/", ephemeral=True)
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

def _now_hhmm_date_in_tz(tz: str):
    now = datetime.now(ZoneInfo(tz))
    return now.strftime("%H:%M"), now.date().isoformat()

# ----- daily refresh (once per day, Berlin time) -----
SCHEDULE_TZ = "Europe/Berlin"

@tasks.loop(time=dtime(hour=4, minute=0, tzinfo=ZoneInfo(SCHEDULE_TZ)))
async def daily_refresh_loop():
    try:
        print(f"[daily_refresh] running at {datetime.now(ZoneInfo(SCHEDULE_TZ)).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        await asyncio.to_thread(sheets.refresh_schedule_preserve_overrides)
        print("[daily_refresh] refresh completed")
    except Exception as e:
        print(f"[daily_refresh] error: {e}")

@daily_refresh_loop.before_loop
async def _wait_daily_refresh_ready():
    await client.wait_until_ready()



@daily_refresh_loop.before_loop
async def _wait_daily_refresh_ready():
    await client.wait_until_ready()

@tasks.loop(minutes=1)
async def reminder_loop():
    try:
        # Gate by global schedule (Berlin-based ✔ day)
        if not await asyncio.to_thread(sheets.is_today_raid_day):
            return

        reminders = await asyncio.to_thread(sheets.get_enabled_reminders)
        if not reminders:
            return

        # Build the list of users to ping whose local time matches now and haven't been pinged today (in THEIR local day)
        to_ping = []
        for r in reminders:
            tz = r.get("tz") or "Europe/Berlin"  # sensible default if user hasn't set tz yet
            try:
                now_local = datetime.now(ZoneInfo(tz))
            except Exception:
                now_local = datetime.now(ZoneInfo("Europe/Berlin"))
            now_hhmm_local = now_local.strftime("%H:%M")
            today_iso_local = now_local.date().isoformat()

            if r["time"] == now_hhmm_local and r.get("last", "") != today_iso_local:
                to_ping.append({**r, "today_iso_local": today_iso_local})

        if not to_ping:
            return

        # Send a single message that mentions all users whose local reminder fired
        channel = client.get_channel(CHANNEL_ID)
        if channel is None:
            return

        mentions = " ".join(f"<@{r['user_id']}>" for r in to_ping)
        await channel.send(f"Wake up, we raidin' today! {mentions}")

        # Mark each user as notified using their local date (so we don't re-ping at 00:xx boundaries)
        for r in to_ping:
            await asyncio.to_thread(sheets.mark_notified, r["user_id"], r["today_iso_local"])

    except Exception as e:
        print(f"[reminder_loop] error: {e}")

@reminder_loop.before_loop
async def _wait_until_ready():
    await client.wait_until_ready()

@client.tree.command(
    name="set_timezone",
    description="Set your timezone (Examples: Europe/Berlin or Europe/London).",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(tz="Your timezone. Example: Europe/Berlin, Europe/London")
async def set_timezone_cmd(interaction: discord.Interaction, tz: str):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in schedule-commands please :/", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    # Validate the tz against the system database
    try:
        ZoneInfo(tz)
    except Exception:
        await interaction.followup.send(
            "Invalid timezone. Examples: Europe/Berlin, Europe/London.",
            ephemeral=True
        )
        return

    await asyncio.to_thread(sheets.set_timezone, interaction.user.id, tz)
    await interaction.followup.send(f"✅ Timezone saved: **{tz}**", ephemeral=True)


def _format_next7(days: list[dict]) -> str:
    if not days:
        return "**Next 7 Raid Days**\nNo upcoming raid days found."
    lines = []
    for d in days:
        # Zeige Anzahl der Anmeldungen; falls lieber Namen: ', '.join(d['names'])
        names_part = f" — {len(d['names'])} signed up" if d["names"] else ""
        lines.append(f"{d['weekday']} {d['date']}{names_part}")
    return "**Next 7 Raid Days**\n" + "\n".join(lines)

async def _upsert_dashboard_message(channel: discord.TextChannel):
    days = await asyncio.to_thread(sheets.get_next_raid_days, 7)
    content = _format_next7(days)

    msg_id = await asyncio.to_thread(sheets.get_next7_message_id)
    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(content=content)
            return
        except discord.NotFound:
            pass  # gelöschte Nachricht -> neu erstellen

    sent = await channel.send(content)
    await asyncio.to_thread(sheets.set_next7_message_id, sent.id)

@tasks.loop(time=dtime(hour=4, minute=5, tzinfo=BERLIN))
async def next7_dashboard_loop():
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await _upsert_dashboard_message(channel)

@next7_dashboard_loop.before_loop
async def _wait_next7_ready():
    await client.wait_until_ready()

@client.tree.command(
    name="next7",
    description="Post/Update the 'Next 7 Raid Days' dashboard now.",
    guild=discord.Object(id=GUILD_ID)
)
async def next7_cmd(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Only in the schedule channel please :/", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await _upsert_dashboard_message(interaction.channel)
        await interaction.followup.send("✅ Dashboard updated.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)
        print(f"[next7_cmd] error: {e}")

client.run(BOT_TOKEN)
























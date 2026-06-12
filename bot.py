import discord
from discord.ext import commands
import logging
import os
import sys
from database import Database, get_rank_name, get_rank_emoji

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("dota-bot")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.invites = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot.db = Database("dota_bot.db")

invite_cache: dict[int, dict[str, int]] = {}

COGS = [
    "cogs.profile",
    "cogs.economy",
    "cogs.tickets",
    "cogs.tournaments",
    "cogs.teams",
]

RANK_ROLE_NAMES = {
    10: "Herald", 20: "Guardian", 30: "Crusader", 40: "Archon",
    50: "Legend", 60: "Ancient", 70: "Divine", 80: "Immortal",
}


async def sync_rank_role(member: discord.Member, rank_tier: int):
    try:
        all_rank_roles = {r.name: r for r in member.guild.roles if r.name in RANK_ROLE_NAMES.values()}
        target_name = RANK_ROLE_NAMES.get((rank_tier // 10) * 10)
        for role_name, role in all_rank_roles.items():
            if role_name == target_name:
                if role not in member.roles:
                    await member.add_roles(role)
            else:
                if role in member.roles:
                    await member.remove_roles(role)
    except Exception as e:
        logger.warning(f"sync_rank_role failed: {e}")


async def setup_hook():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"Loaded: {cog}")
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"Sync error: {e}")

bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    logger.info(f"Bot ready: {bot.user} (ID: {bot.user.id})")
    for guild in bot.guilds:
        try:
            invites = await guild.fetch_invites()
            invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except Exception as e:
            logger.warning(f"No invite perms in {guild.name}: {e}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="Dota 2 | /profile /lfg /tournaments"
        )
    )


@bot.event
async def on_invite_create(invite):
    gid = invite.guild.id
    if gid not in invite_cache:
        invite_cache[gid] = {}
    invite_cache[gid][invite.code] = invite.uses or 0


@bot.event
async def on_invite_delete(invite):
    gid = invite.guild.id
    if gid in invite_cache:
        invite_cache[gid].pop(invite.code, None)


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    bot.db.ensure_user(guild.id, member.id)
    inviter_id = None
    used_code = None
    try:
        new_invites = await guild.fetch_invites()
        new_cache = {inv.code: inv.uses for inv in new_invites}
        old_cache = invite_cache.get(guild.id, {})
        for inv in new_invites:
            if inv.uses > old_cache.get(inv.code, 0) and inv.inviter:
                inviter_id = inv.inviter.id
                used_code = inv.code
                break
        invite_cache[guild.id] = new_cache
    except Exception as e:
        logger.warning(f"Invite detection error: {e}")

    welcome_embed = discord.Embed(
        title="Новый союзник на сервере!",
        description=(
            f"Добро пожаловать, {member.mention}!\n\n"
            "Начни с `/setup-profile` — укажи свой MMR и роли.\n"
            "Найди команду через `/lfg` и `/find-players`.\n"
            "Участвуй в турнирах через `/tournaments`!"
        ),
        color=0xc23c2a
    )
    welcome_embed.set_thumbnail(url=member.display_avatar.url)

    if inviter_id:
        bot.db.ensure_user(guild.id, inviter_id)
        bot.db.add_invite(guild.id, inviter_id, member.id, used_code or "")
        inviter = guild.get_member(inviter_id)
        inviter_name = inviter.display_name if inviter else f"<@{inviter_id}>"
        welcome_embed.add_field(
            name="Пригласил",
            value=f"**{inviter_name}** получил +50 Aegis и +10 Bloodstone!"
        )

    config = bot.db.get_config(guild.id)
    channel = None
    if config and config["announce_channel"]:
        channel = guild.get_channel(config["announce_channel"])
    if not channel:
        channel = guild.system_channel
    if channel:
        try:
            await channel.send(embed=welcome_embed)
        except Exception:
            pass


@bot.event
async def on_member_remove(member: discord.Member):
    bot.db.member_left(member.guild.id, member.id)
    try:
        invites = await member.guild.fetch_invites()
        invite_cache[member.guild.id] = {inv.code: inv.uses for inv in invites}
    except Exception:
        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    guild_id = message.guild.id
    user_id = message.author.id
    bot.db.ensure_user(guild_id, user_id)
    gained = bot.db.process_message(guild_id, user_id)
    if gained:
        _, _, leveled_up, new_level = gained
        if leveled_up:
            embed = discord.Embed(
                title="Level Up!",
                description=f"{message.author.mention} достиг **{new_level} уровня**!",
                color=0xFFD700
            )
            bonus = new_level * 25
            bot.db.add_aegis(guild_id, user_id, bonus)
            embed.add_field(name="Бонус", value=f"+{bonus} Aegis")
            await message.channel.send(embed=embed, delete_after=15)
    await bot.process_commands(message)


@bot.event
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.errors.MissingPermissions):
        await interaction.response.send_message("У тебя нет прав для этой команды.", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        try:
            await interaction.response.send_message("Произошла ошибка. Попробуй ещё раз.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="help", description="Список всех команд бота")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Dota 2 Bot — Команды", description="Все команды через `/`", color=0xc23c2a)
    embed.add_field(name="Профиль", value="`/profile` `/setup-profile` `/set-roles` `/dota-stats`", inline=False)
    embed.add_field(name="Команды / LFG", value="`/lfg` `/find-players` `/teams` `/create-team` `/join-team` `/my-team`", inline=False)
    embed.add_field(name="Турниры", value="`/tournaments` `/tournament-join` `/tournament-info`", inline=False)
    embed.add_field(name="Экономика", value="`/balance` `/daily` `/pay` `/shop` `/buy` `/top`", inline=False)
    embed.add_field(name="Тикеты", value="`/ticket` `/withdrawal-info` `/my-tickets`", inline=False)
    embed.set_footer(text="Bloodstone — за инвайты. Aegis — за активность.")
    await interaction.response.send_message(embed=embed)


TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    logger.error("DISCORD_TOKEN not found in environment!")
    sys.exit(1)

bot.run(TOKEN)

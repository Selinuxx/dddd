import discord
from discord.ext import commands
import logging
import os
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

# Кэш инвайтов {guild_id: {code: uses}}
invite_cache: dict[int, dict[str, int]] = {}

COGS = [
    "cogs.profile",
    "cogs.economy",
    "cogs.tickets",
    "cogs.tournaments",
    "cogs.teams",
]

# Dota 2 ранги: Discord-роль автоматически выдаётся по rank_tier
RANK_ROLE_NAMES = {
    10: "Herald",
    20: "Guardian",
    30: "Crusader",
    40: "Archon",
    50: "Legend",
    60: "Ancient",
    70: "Divine",
    80: "Immortal",
}


async def sync_rank_role(member: discord.Member, rank_tier: int):
    """Выдать/убрать роль по рангу Dota 2."""
    try:
        all_rank_roles = {
            r.name: r for r in member.guild.roles
            if r.name in RANK_ROLE_NAMES.values()
        }
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


@bot.event
async def on_ready():
    logger.info(f"🎮 Бот запущен: {bot.user} (ID: {bot.user.id})")

    # Загружаем коги
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"  ✅ Загружен: {cog}")
        except Exception as e:
            logger.error(f"  ❌ Ошибка загрузки {cog}: {e}")

    # Кэшируем инвайты
    for guild in bot.guilds:
        try:
            invites = await guild.fetch_invites()
            invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except Exception as e:
            logger.warning(f"Нет прав на инвайты в {guild.name}: {e}")

    # Синхронизируем slash-команды
    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ Синхронизировано {len(synced)} slash-команд")
    except Exception as e:
        logger.error(f"❌ Ошибка синхронизации: {e}")

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
            old_uses = old_cache.get(inv.code, 0)
            if inv.uses > old_uses and inv.inviter:
                inviter_id = inv.inviter.id
                used_code = inv.code
                break

        invite_cache[guild.id] = new_cache
    except Exception as e:
        logger.warning(f"on_member_join invite detection error: {e}")

    welcome_embed = discord.Embed(
        title="⚔️ Новый союзник на сервере!",
        description=f"Добро пожаловать, {member.mention}!\n\n"
                    "Начни с `/setup-profile` — укажи свой MMR и роли.\n"
                    "Найди команду через `/lfg` и `/find-players`.\n"
                    "Участвуй в турнирах через `/tournaments`!",
        color=0xc23c2a
    )
    welcome_embed.set_thumbnail(url=member.display_avatar.url)

    if inviter_id:
        bot.db.ensure_user(guild.id, inviter_id)
        bot.db.add_invite(guild.id, inviter_id, member.id, used_code or "")
        inviter = guild.get_member(inviter_id)
        inviter_name = inviter.display_name if inviter else f"<@{inviter_id}>"
        welcome_embed.add_field(
            name="🔗 Пригласил",
            value=f"**{inviter_name}** получил +50 Aegis и +10 Bloodstone!"
        )
        logger.info(f"Invite: {member} ← {inviter_name} ({used_code})")

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
                title="🎉 Level Up!",
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
        await interaction.response.send_message("❌ У тебя нет прав для этой команды.", ephemeral=True)
    else:
        logger.error(f"Slash command error: {error}")
        try:
            await interaction.response.send_message(
                "❌ Произошла ошибка. Попробуй ещё раз.", ephemeral=True
            )
        except Exception:
            pass


# ── Команда помощи ────────────────────────────────────────────────────────────

@bot.tree.command(name="help", description="Список всех команд бота")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎮 Dota 2 Bot — Команды",
        description="Все команды используются через `/`",
        color=0xc23c2a
    )
    embed.add_field(name="👤 Профиль", value=(
        "`/profile` — Твой профиль\n"
        "`/setup-profile` — Настроить MMR, Steam, язык\n"
        "`/set-roles` — Выбрать роли (carry/mid/...)\n"
        "`/dota-stats` — Статистика из OpenDota\n"
    ), inline=False)
    embed.add_field(name="🔍 Поиск команды", value=(
        "`/lfg` — Включить поиск команды\n"
        "`/find-players` — Найти игроков по фильтрам\n"
        "`/teams` — Список команд\n"
        "`/create-team` — Создать команду\n"
        "`/join-team` — Вступить в команду\n"
        "`/my-team` — Моя команда\n"
    ), inline=False)
    embed.add_field(name="🏆 Турниры", value=(
        "`/tournaments` — Открытые турниры\n"
        "`/tournament-join` — Зарегистрироваться\n"
        "`/tournament-info` — Участники турнира\n"
    ), inline=False)
    embed.add_field(name="💰 Экономика", value=(
        "`/balance` — Aegis и Bloodstone\n"
        "`/daily` — Ежедневная награда (до x7)\n"
        "`/pay` — Перевести Aegis\n"
        "`/shop` — Магазин ролей\n"
        "`/buy` — Купить роль\n"
        "`/top` — Таблица лидеров\n"
    ), inline=False)
    embed.add_field(name="🎫 Тикеты", value=(
        "`/ticket withdrawal` — Вывести деньги за инвайты\n"
        "`/ticket support` — Вопрос к администрации\n"
        "`/ticket report` — Жалоба на игрока\n"
        "`/withdrawal-info` — Как работает вывод\n"
        "`/my-tickets` — Мои тикеты\n"
    ), inline=False)
    embed.set_footer(text="💎 Bloodstone зарабатывается за инвайты. 🏆 Aegis — за активность.")
    await interaction.response.send_message(embed=embed)


TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Переменная DISCORD_TOKEN не задана! Создай .env файл или задай переменную окружения.")

bot.run(TOKEN)

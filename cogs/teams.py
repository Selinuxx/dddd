import discord
from discord.ext import commands
from discord import app_commands
from database import get_rank_name, get_rank_emoji


class TeamsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="create-team", description="Создать команду")
    @app_commands.describe(name="Название команды", description="Описание", min_mmr="Минимальный MMR для вступления")
    async def create_team(self, interaction: discord.Interaction,
                           name: str, description: str = "", min_mmr: int = 0):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)

        existing = db.get_user_team(interaction.guild_id, interaction.user.id)
        if existing:
            await interaction.response.send_message(
                f"❌ Ты уже состоишь в команде **{existing['name']}**. Выйди через `/leave-team`.",
                ephemeral=True
            )
            return

        result = db.create_team(interaction.guild_id, interaction.user.id, name, description, min_mmr)
        if result["success"]:
            embed = discord.Embed(
                title="⚔️ Команда создана!",
                description=(
                    f"**{name}** (ID: {result['team_id']})\n"
                    f"{description}\n\n"
                    f"Минимальный MMR: {min_mmr if min_mmr > 0 else 'Любой'}\n"
                    f"Пригласи игроков: `/teams` → `/join-team {result['team_id']}`"
                ),
                color=0x5865F2
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"❌ {result['error']}", ephemeral=True)

    @app_commands.command(name="teams", description="Список команд на сервере")
    async def teams(self, interaction: discord.Interaction):
        db = self.bot.db
        teams = db.get_teams(interaction.guild_id)
        embed = discord.Embed(title="⚔️ Команды сервера", color=0x5865F2)
        if not teams:
            embed.description = "Команд пока нет. Создай первую: `/create-team`"
        else:
            for t in teams[:10]:
                captain = interaction.guild.get_member(t["captain_id"])
                cap_name = captain.display_name if captain else f"<@{t['captain_id']}>"
                mmr_str = f"MMR {t['min_mmr']}+" if t["min_mmr"] > 0 else "Любой MMR"
                embed.add_field(
                    name=f"#{t['id']} ⚔️ {t['name']} ({t['member_count']}/5)",
                    value=(
                        f"Капитан: {cap_name} | {mmr_str}\n"
                        f"{t['description'] or ''}\n"
                        f"Вступить: `/join-team {t['id']}`"
                    ),
                    inline=False
                )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="team-info", description="Информация о команде")
    @app_commands.describe(team_id="ID команды из /teams")
    async def team_info(self, interaction: discord.Interaction, team_id: int):
        db = self.bot.db
        team = db.get_team(team_id)
        if not team or team["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Команда не найдена.", ephemeral=True)
            return

        members = db.get_team_members(team_id)
        embed = discord.Embed(title=f"⚔️ {team['name']}", description=team["description"] or "", color=0x5865F2)
        captain = interaction.guild.get_member(team["captain_id"])
        embed.add_field(name="👑 Капитан", value=captain.mention if captain else f"<@{team['captain_id']}>", inline=True)
        embed.add_field(name="📊 Мин. MMR", value=str(team["min_mmr"]) if team["min_mmr"] > 0 else "Любой", inline=True)
        embed.add_field(name="👥 Состав", value=str(len(members)) + "/5", inline=True)

        members_str = ""
        for m in members:
            member = interaction.guild.get_member(m["user_id"])
            user_data = db.get_user(interaction.guild_id, m["user_id"])
            if user_data:
                rank_str = f"{get_rank_emoji(user_data['rank_tier'])} {get_rank_name(user_data['rank_tier'])}"
                mmr = f"{user_data['mmr']:,} MMR" if user_data["mmr"] > 0 else "—"
                name = member.mention if member else f"<@{m['user_id']}>"
                crown = " 👑" if m["user_id"] == team["captain_id"] else ""
                members_str += f"{name}{crown} — {rank_str} | {mmr}\n"
        embed.add_field(name="🎮 Игроки", value=members_str or "—", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="join-team", description="Вступить в команду")
    @app_commands.describe(team_id="ID команды из /teams")
    async def join_team(self, interaction: discord.Interaction, team_id: int):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)

        existing = db.get_user_team(interaction.guild_id, interaction.user.id)
        if existing:
            await interaction.response.send_message(
                f"❌ Ты уже в команде **{existing['name']}**.", ephemeral=True
            )
            return

        team = db.get_team(team_id)
        if not team or team["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Команда не найдена.", ephemeral=True)
            return

        if team["min_mmr"] > 0:
            user_data = db.get_user(interaction.guild_id, interaction.user.id)
            if user_data["mmr"] < team["min_mmr"]:
                await interaction.response.send_message(
                    f"❌ Твой MMR ({user_data['mmr']:,}) ниже минимума для этой команды ({team['min_mmr']:,}).",
                    ephemeral=True
                )
                return

        result = db.join_team(team_id, interaction.user.id)
        if result["success"]:
            embed = discord.Embed(
                title=f"✅ Добро пожаловать в {team['name']}!",
                description=f"Используй `/team-info {team_id}` чтобы увидеть состав.",
                color=0x57F287
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"❌ {result['error']}", ephemeral=True)

    @app_commands.command(name="leave-team", description="Покинуть команду")
    async def leave_team(self, interaction: discord.Interaction):
        db = self.bot.db
        team = db.get_user_team(interaction.guild_id, interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Ты не состоишь в команде.", ephemeral=True)
            return

        if team["captain_id"] == interaction.user.id:
            members = db.get_team_members(team["id"])
            if len(members) > 1:
                await interaction.response.send_message(
                    "❌ Ты капитан. Сначала передай капитанство через `/transfer-captain` или распусти команду через `/disband-team`.",
                    ephemeral=True
                )
                return
            db.delete_team(team["id"])
            await interaction.response.send_message(f"✅ Команда **{team['name']}** распущена.", ephemeral=True)
        else:
            db.leave_team(team["id"], interaction.user.id)
            await interaction.response.send_message(f"✅ Ты покинул команду **{team['name']}**.", ephemeral=True)

    @app_commands.command(name="disband-team", description="Распустить команду (только капитан)")
    async def disband_team(self, interaction: discord.Interaction):
        db = self.bot.db
        team = db.get_user_team(interaction.guild_id, interaction.user.id)
        if not team:
            await interaction.response.send_message("❌ Ты не состоишь в команде.", ephemeral=True)
            return
        if team["captain_id"] != interaction.user.id:
            await interaction.response.send_message("❌ Только капитан может распустить команду.", ephemeral=True)
            return
        db.delete_team(team["id"])
        await interaction.response.send_message(f"✅ Команда **{team['name']}** распущена.", ephemeral=True)

    @app_commands.command(name="my-team", description="Информация о твоей команде")
    async def my_team(self, interaction: discord.Interaction):
        db = self.bot.db
        team = db.get_user_team(interaction.guild_id, interaction.user.id)
        if not team:
            await interaction.response.send_message(
                "❌ Ты не состоишь в команде. Создай: `/create-team` или вступи: `/teams`",
                ephemeral=True
            )
            return
        # Переиспользуем team_info
        await self.team_info.callback(self, interaction, team["id"])


async def setup(bot):
    await bot.add_cog(TeamsCog(bot))

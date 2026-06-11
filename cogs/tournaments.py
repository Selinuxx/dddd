import discord
from discord.ext import commands
from discord import app_commands


class TournamentsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tournaments", description="Список открытых турниров")
    async def tournaments(self, interaction: discord.Interaction):
        db = self.bot.db
        ts = db.get_tournaments(interaction.guild_id, "open")
        embed = discord.Embed(title="🏆 Открытые турниры", color=0xFFD700)
        if not ts:
            embed.description = "Пока турниров нет. Следи за анонсами!"
        else:
            for t in ts:
                parts = db.get_tournament_participants(t["id"])
                embed.add_field(
                    name=f"#{t['id']} — {t['name']}",
                    value=(
                        f"{t['description'] or ''}\n"
                        f"🎫 Взнос: **{t['ticket_price']} Aegis** | "
                        f"🏅 Призовой: **{t['prize_pool']}** | "
                        f"👥 {len(parts)}/{t['max_teams']} команд\n"
                        f"Регистрация: `/tournament-join {t['id']} Название команды`"
                    ),
                    inline=False
                )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="tournament-join", description="Зарегистрироваться на турнир")
    @app_commands.describe(tournament_id="ID турнира из /tournaments", team_name="Название твоей команды")
    async def tournament_join(self, interaction: discord.Interaction,
                               tournament_id: int, team_name: str):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)
        result = db.register_tournament(tournament_id, interaction.guild_id,
                                         interaction.user.id, team_name)
        if result["success"]:
            t = db.get_tournament(tournament_id)
            embed = discord.Embed(
                title="✅ Регистрация подтверждена!",
                description=(
                    f"Ты зарегистрирован на **{t['name']}** за команду **{team_name}**.\n"
                    f"Списано **{t['ticket_price']} Aegis** с баланса."
                ),
                color=0x57F287
            )
        else:
            embed = discord.Embed(title="❌ Ошибка", description=result["error"], color=0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="tournament-info", description="Участники турнира")
    @app_commands.describe(tournament_id="ID турнира")
    async def tournament_info(self, interaction: discord.Interaction, tournament_id: int):
        db = self.bot.db
        t = db.get_tournament(tournament_id)
        if not t:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return
        parts = db.get_tournament_participants(tournament_id)
        status_labels = {"open": "🟢 Открыт", "closed": "🔴 Закрыт", "finished": "🏁 Завершён"}
        embed = discord.Embed(
            title=f"🏆 {t['name']}",
            description=t["description"] or "",
            color=0xFFD700
        )
        embed.add_field(name="Статус", value=status_labels.get(t["status"], t["status"]), inline=True)
        embed.add_field(name="🎫 Взнос", value=f"{t['ticket_price']} Aegis", inline=True)
        embed.add_field(name="🏅 Призовой", value=t["prize_pool"], inline=True)
        embed.add_field(name=f"👥 Участники ({len(parts)}/{t['max_teams']})",
                        value="\n".join(f"• <@{p['user_id']}> — {p['team_name']}" for p in parts) or "Нет",
                        inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="create-tournament", description="[Админ] Создать турнир")
    @app_commands.describe(
        name="Название", description="Описание",
        ticket_price="Стоимость билета (Aegis)", prize_pool="Призовой фонд",
        max_teams="Максимум команд"
    )
    @app_commands.default_permissions(administrator=True)
    async def create_tournament(self, interaction: discord.Interaction,
                                 name: str, ticket_price: int,
                                 description: str = "", prize_pool: str = "TBD",
                                 max_teams: int = 8):
        db = self.bot.db
        tid = db.create_tournament(interaction.guild_id, name, description,
                                   ticket_price, prize_pool, max_teams)
        config = db.get_config(interaction.guild_id)
        embed = discord.Embed(
            title="🏆 Турнир создан!",
            description=(
                f"**{name}** (ID: {tid})\n"
                f"🎫 Взнос: {ticket_price} Aegis | 🏅 Приз: {prize_pool} | 👥 Мест: {max_teams}\n\n"
                f"Регистрация: `/tournament-join {tid} Название команды`"
            ),
            color=0xFFD700
        )
        # Анонс
        if config and config["announce_channel"]:
            ann = interaction.guild.get_channel(config["announce_channel"])
            if ann:
                ann_embed = discord.Embed(
                    title=f"🏆 Новый турнир: {name}",
                    description=(
                        f"{description}\n\n"
                        f"🎫 Взнос: **{ticket_price} Aegis** | 🏅 Приз: **{prize_pool}**\n"
                        f"👥 Мест: **{max_teams}**\n\n"
                        f"Зарегистрируйся: `/tournament-join {tid} Название команды`"
                    ),
                    color=0xFFD700
                )
                await ann.send(embed=ann_embed)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="close-tournament", description="[Админ] Закрыть регистрацию / завершить турнир")
    @app_commands.describe(tournament_id="ID турнира", status="Новый статус")
    @app_commands.choices(status=[
        app_commands.Choice(name="🔴 Закрыть регистрацию", value="closed"),
        app_commands.Choice(name="🏁 Завершить турнир",    value="finished"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def close_tournament(self, interaction: discord.Interaction,
                                tournament_id: int, status: str):
        self.bot.db.update_tournament_status(tournament_id, status)
        await interaction.response.send_message(f"✅ Турнир #{tournament_id} → статус: `{status}`", ephemeral=True)


async def setup(bot):
    await bot.add_cog(TournamentsCog(bot))

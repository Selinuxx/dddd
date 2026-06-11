import discord
from discord.ext import commands
from discord import app_commands
from database import get_rank_name, get_rank_emoji


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance", description="Твой баланс Aegis и Bloodstone")
    @app_commands.describe(member="Участник (по умолчанию — ты)")
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        db = self.bot.db
        db.ensure_user(interaction.guild_id, target.id)
        d = db.get_user(interaction.guild_id, target.id)
        config = db.get_config(interaction.guild_id)
        rate = config["bloodstone_rate"] if config else 0.01
        usd = round(d["bloodstone"] * rate, 2)

        embed = discord.Embed(title=f"💰 Баланс {target.display_name}", color=0x1a1a2e)
        embed.add_field(name="🏆 Aegis", value=f"**{d['aegis']:,}**\n*Турниры, магазин*", inline=True)
        embed.add_field(name="💎 Bloodstone", value=f"**{d['bloodstone']:,}**\n*≈ ${usd} (вывод)*", inline=True)
        embed.set_footer(text="Aegis зарабатывается за активность. Bloodstone — за инвайты.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="Ежедневная награда Aegis")
    async def daily(self, interaction: discord.Interaction):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)
        result = db.claim_daily(interaction.guild_id, interaction.user.id)

        if result["success"]:
            streak = result["streak"]
            reward = result["reward"]
            embed = discord.Embed(title="🎁 Ежедневная награда!", color=0x57F287)
            embed.add_field(name="🏆 Получено", value=f"**{reward} Aegis**", inline=True)
            embed.add_field(name="🔥 Стрик", value=f"**{streak}** дней", inline=True)
            if streak >= 7:
                embed.add_field(name="⚡ Максимальный стрик!", value="x7 бонус активен!", inline=False)
            embed.set_footer(text="Заходи каждый день для максимальной награды (x7 на 7-й день)")
        else:
            s = result["seconds_left"]
            h, m = s // 3600, (s % 3600) // 60
            embed = discord.Embed(
                title="⏳ Уже получал сегодня",
                description=f"Следующая награда через **{h}ч {m}м**",
                color=0xED4245
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pay", description="Перевести Aegis другому игроку")
    @app_commands.describe(member="Кому", amount="Сколько Aegis")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("❌ Сумма должна быть > 0.", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ Нельзя платить самому себе.", ephemeral=True)
            return
        db = self.bot.db
        guild_id = interaction.guild_id
        db.ensure_user(guild_id, interaction.user.id)
        db.ensure_user(guild_id, member.id)
        sender = db.get_user(guild_id, interaction.user.id)
        if sender["aegis"] < amount:
            await interaction.response.send_message(
                f"❌ Недостаточно Aegis. У тебя: **{sender['aegis']:,}**", ephemeral=True
            )
            return
        db.add_aegis(guild_id, interaction.user.id, -amount)
        db.add_aegis(guild_id, member.id, amount)
        embed = discord.Embed(
            title="💸 Перевод выполнен",
            description=f"{interaction.user.mention} → {member.mention}: **{amount:,} Aegis**",
            color=0x57F287
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="top", description="Таблица лидеров")
    @app_commands.describe(by="Категория")
    @app_commands.choices(by=[
        app_commands.Choice(name="⭐ По уровню",   value="xp"),
        app_commands.Choice(name="🏆 По Aegis",    value="aegis"),
        app_commands.Choice(name="📊 По MMR",      value="mmr"),
        app_commands.Choice(name="🔗 По инвайтам", value="invites"),
    ])
    async def top(self, interaction: discord.Interaction, by: str = "xp"):
        db = self.bot.db
        results = db.get_leaderboard(interaction.guild_id, by, 10)

        titles = {
            "xp":      "⭐ Топ по уровню",
            "aegis":   "🏆 Топ по Aegis",
            "mmr":     "📊 Топ по MMR",
            "invites": "🔗 Топ по инвайтам",
        }
        embed = discord.Embed(title=titles.get(by, "Топ"), color=0xFFD700)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(results):
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            m = interaction.guild.get_member(row["user_id"])
            name = m.display_name if m else f"<@{row['user_id']}>"
            if by == "xp":
                val = f"Ур. **{row['level']}** | {row['xp']:,} XP"
            elif by == "aegis":
                val = f"🏆 **{row['aegis']:,}** Aegis"
            elif by == "mmr":
                val = f"{get_rank_emoji(row['rank_tier'])} **{row['mmr']:,}** MMR — {get_rank_name(row['rank_tier'])}"
            else:
                val = f"🔗 **{row['count']}** активных инвайтов"
            lines.append(f"{medal} **{name}** — {val}")
        embed.description = "\n".join(lines) if lines else "Пока никого нет."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop", description="Магазин ролей за Aegis")
    async def shop(self, interaction: discord.Interaction):
        db = self.bot.db
        items = db.get_shop_items(interaction.guild_id)
        embed = discord.Embed(title="🏪 Магазин", color=0x1a1a2e)
        embed.description = "Трать Aegis на эксклюзивные роли сервера!\n\n"
        if items:
            for item in items:
                role = interaction.guild.get_role(item["role_id"])
                if role:
                    embed.add_field(
                        name=f"#{item['id']} — {item['name']}",
                        value=f"{item['description'] or ''}\nРоль: {role.mention} | Цена: **{item['price']} Aegis**\n`/buy {item['id']}`",
                        inline=False
                    )
        else:
            embed.description += "Магазин пуст. Администратор добавит товары командой `/additem`."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Купить роль из магазина")
    @app_commands.describe(item_id="ID товара из /shop")
    async def buy(self, interaction: discord.Interaction, item_id: int):
        db = self.bot.db
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        db.ensure_user(guild_id, user_id)
        item = db.get_shop_item(item_id)
        if not item or item["guild_id"] != guild_id:
            await interaction.response.send_message("❌ Товар не найден.", ephemeral=True)
            return
        user = db.get_user(guild_id, user_id)
        if user["aegis"] < item["price"]:
            await interaction.response.send_message(
                f"❌ Недостаточно Aegis. Нужно: **{item['price']}**, у тебя: **{user['aegis']}**",
                ephemeral=True
            )
            return
        role = interaction.guild.get_role(item["role_id"])
        if not role:
            await interaction.response.send_message("❌ Роль не найдена на сервере.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("❌ У тебя уже есть эта роль.", ephemeral=True)
            return
        db.add_aegis(guild_id, user_id, -item["price"])
        await interaction.user.add_roles(role)
        embed = discord.Embed(
            title="✅ Покупка успешна!",
            description=f"Ты купил **{item['name']}** → роль {role.mention}",
            color=0x57F287
        )
        embed.add_field(name="Списано", value=f"{item['price']} Aegis")
        await interaction.response.send_message(embed=embed)

    # Админ-команды
    @app_commands.command(name="give-aegis", description="[Админ] Выдать Aegis игроку")
    @app_commands.default_permissions(administrator=True)
    async def give_aegis(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        self.bot.db.ensure_user(interaction.guild_id, member.id)
        self.bot.db.add_aegis(interaction.guild_id, member.id, amount)
        await interaction.response.send_message(
            f"✅ {member.mention} получил **{amount:,} Aegis**.", ephemeral=True
        )

    @app_commands.command(name="additem", description="[Админ] Добавить товар в магазин")
    @app_commands.describe(role="Роль", name="Название", description="Описание", price="Цена в Aegis")
    @app_commands.default_permissions(administrator=True)
    async def additem(self, interaction: discord.Interaction,
                      role: discord.Role, name: str, price: int, description: str = ""):
        item_id = self.bot.db.add_shop_item(interaction.guild_id, name, description, role.id, price)
        await interaction.response.send_message(
            f"✅ Добавлен товар **{name}** (роль {role.mention}) за **{price} Aegis** (ID: {item_id})",
            ephemeral=True
        )

    @app_commands.command(name="removeitem", description="[Админ] Удалить товар из магазина")
    @app_commands.describe(item_id="ID товара")
    @app_commands.default_permissions(administrator=True)
    async def removeitem(self, interaction: discord.Interaction, item_id: int):
        self.bot.db.remove_shop_item(item_id)
        await interaction.response.send_message(f"✅ Товар #{item_id} удалён.", ephemeral=True)

    @app_commands.command(name="setup-server", description="[Админ] Настроить параметры сервера")
    @app_commands.describe(
        announce_channel="Канал для анонсов",
        admin_role="Роль администраторов",
        bloodstone_rate="Курс Bloodstone в USD (по умолчанию 0.01)",
        min_withdrawal="Минимум Bloodstone для вывода (по умолчанию 500)"
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_server(self, interaction: discord.Interaction,
                            announce_channel: discord.TextChannel = None,
                            admin_role: discord.Role = None,
                            bloodstone_rate: float = None,
                            min_withdrawal: int = None):
        db = self.bot.db
        updates = {}
        if announce_channel: updates["announce_channel"] = announce_channel.id
        if admin_role: updates["admin_role"] = admin_role.id
        if bloodstone_rate: updates["bloodstone_rate"] = bloodstone_rate
        if min_withdrawal: updates["min_withdrawal"] = min_withdrawal

        if updates:
            db.set_config(interaction.guild_id, **updates)

        config = db.get_config(interaction.guild_id)
        rate = config["bloodstone_rate"]
        min_w = config["min_withdrawal"]
        ann = f"<#{config['announce_channel']}>" if config["announce_channel"] else "Не задан"
        adm = f"<@&{config['admin_role']}>" if config["admin_role"] else "Не задана"

        embed = discord.Embed(title="⚙️ Настройки сервера", color=0x5865F2)
        embed.add_field(name="📢 Канал анонсов", value=ann, inline=True)
        embed.add_field(name="🛡️ Роль администратора", value=adm, inline=True)
        embed.add_field(name="💎 Курс Bloodstone", value=f"1 BS = ${rate}", inline=True)
        embed.add_field(name="💳 Мин. вывод", value=f"{min_w:,} Bloodstone (~${round(min_w * rate, 2)})", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))

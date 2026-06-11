import discord
from discord.ext import commands
from discord import app_commands


TICKET_TYPES = {
    "withdrawal": "💸 Вывод средств",
    "support":    "🛠️ Поддержка",
    "report":     "🚨 Жалоба",
}


class TicketView(discord.ui.View):
    """Кнопки внутри тикет-канала."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = interaction.client.db
        ticket = db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ Тикет не найден.", ephemeral=True)
            return

        # Только автор или админ
        is_admin = interaction.user.guild_permissions.administrator
        is_author = ticket["user_id"] == interaction.user.id
        if not is_admin and not is_author:
            await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
            return

        db.close_ticket(ticket["id"])
        embed = discord.Embed(
            title="🔒 Тикет закрыт",
            description=f"Закрыт пользователем {interaction.user.mention}",
            color=0xED4245
        )
        await interaction.response.send_message(embed=embed)
        await interaction.channel.edit(name=f"closed-{interaction.channel.name}")
        # Запрещаем писать пользователю
        author = interaction.guild.get_member(ticket["user_id"])
        if author:
            await interaction.channel.set_permissions(author, send_messages=False)

    @discord.ui.button(label="🗑️ Удалить канал", style=discord.ButtonStyle.secondary, custom_id="delete_ticket_channel")
    async def delete_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только для администраторов.", ephemeral=True)
            return
        await interaction.response.send_message("Удаляю канал через 3 секунды...")
        import asyncio
        await asyncio.sleep(3)
        await interaction.channel.delete()


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(TicketView())  # persistent view

    # ── /ticket ───────────────────────────────────────────────────────────────

    @app_commands.command(name="ticket", description="Создать тикет (поддержка, вывод средств, жалоба)")
    @app_commands.describe(
        ticket_type="Тип тикета",
        details="Подробности / реквизиты для вывода"
    )
    @app_commands.choices(ticket_type=[
        app_commands.Choice(name="💸 Вывод средств (Bloodstone → деньги)", value="withdrawal"),
        app_commands.Choice(name="🛠️ Поддержка / вопрос",                 value="support"),
        app_commands.Choice(name="🚨 Жалоба на игрока",                   value="report"),
    ])
    async def ticket(self, interaction: discord.Interaction,
                     ticket_type: str, details: str = ""):
        db = self.bot.db
        guild = interaction.guild
        user = interaction.user
        db.ensure_user(guild.id, user.id)
        user_data = db.get_user(guild.id, user.id)
        config = db.get_config(guild.id)

        # Проверка для вывода
        amount = 0
        if ticket_type == "withdrawal":
            bs = user_data["bloodstone"]
            min_w = config["min_withdrawal"] if config else 500
            rate = config["bloodstone_rate"] if config else 0.01
            if bs < min_w:
                await interaction.response.send_message(
                    f"❌ Недостаточно Bloodstone для вывода.\n"
                    f"У тебя: **{bs}** | Минимум: **{min_w}**\n"
                    f"Приглашай людей на сервер, чтобы заработать Bloodstone!",
                    ephemeral=True
                )
                return
            if not details:
                await interaction.response.send_message(
                    "❌ Укажи реквизиты для вывода в поле `details` (например: Binance UID, номер карты, кошелёк).",
                    ephemeral=True
                )
                return
            amount = bs
            usd = round(bs * rate, 2)

        # Находим / создаём категорию для тикетов
        category = None
        if config and config["ticket_category"]:
            category = guild.get_channel(config["ticket_category"])
        if not category:
            # Создаём категорию автоматически если нет
            category = discord.utils.get(guild.categories, name="📋 ТИКЕТЫ")
            if not category:
                category = await guild.create_category("📋 ТИКЕТЫ")
                db.set_config(guild.id, ticket_category=category.id)

        # Создаём канал тикета
        ticket_id = db.create_ticket(guild.id, user.id, ticket_type, amount, details)
        channel_name = f"{ticket_type}-{user.name}-{ticket_id}"[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        # Дать доступ роли администраторов
        if config and config["admin_role"]:
            admin_role = guild.get_role(config["admin_role"])
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await guild.create_text_channel(
            channel_name, category=category, overwrites=overwrites
        )
        db.update_ticket_channel(ticket_id, channel.id)

        # Сообщение внутри тикета
        type_label = TICKET_TYPES.get(ticket_type, ticket_type)
        embed = discord.Embed(
            title=f"{type_label} — Тикет #{ticket_id}",
            color=0x5865F2
        )
        embed.add_field(name="👤 Пользователь", value=user.mention, inline=True)
        embed.add_field(name="🏷️ Тип", value=type_label, inline=True)

        if ticket_type == "withdrawal":
            rate = config["bloodstone_rate"] if config else 0.01
            usd = round(amount * rate, 2)
            embed.add_field(
                name="💎 Сумма вывода",
                value=f"**{amount:,} Bloodstone** (~**${usd}**)",
                inline=False
            )
            embed.add_field(name="💳 Реквизиты", value=f"```{details}```", inline=False)
            embed.add_field(
                name="📋 Инструкция для администратора",
                value=(
                    "1. Проверь реквизиты\n"
                    "2. Выполни выплату\n"
                    f"3. Спиши Bloodstone: `/admin-deduct @{user.name} {amount}`\n"
                    "4. Закрой тикет кнопкой ниже"
                ),
                inline=False
            )
            # Блокируем bloodstone на время рассмотрения
            db.add_bloodstone(guild.id, user.id, -amount)
            embed.set_footer(text=f"⚠️ {amount:,} Bloodstone временно заморожены до закрытия тикета")

        elif ticket_type == "report":
            embed.add_field(name="📝 Жалоба", value=details or "Подробности не указаны", inline=False)

        else:
            embed.add_field(name="📝 Вопрос", value=details or "Подробности не указаны", inline=False)

        embed.set_footer(text="Администратор скоро ответит. Не закрывай тикет до решения.")

        await channel.send(
            content=f"{user.mention} — твой тикет создан!",
            embed=embed,
            view=TicketView()
        )

        # Пинг в канал объявлений
        if config and config["announce_channel"]:
            ann = guild.get_channel(config["announce_channel"])
            if ann and ticket_type == "withdrawal":
                await ann.send(
                    f"📬 Новый тикет на вывод #{ticket_id} от {user.mention} — {channel.mention}"
                )

        await interaction.response.send_message(
            f"✅ Тикет #{ticket_id} создан → {channel.mention}",
            ephemeral=True
        )

    # ── /my-tickets ───────────────────────────────────────────────────────────

    @app_commands.command(name="my-tickets", description="Мои тикеты")
    async def my_tickets(self, interaction: discord.Interaction):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)
        tickets = db.get_user_tickets(interaction.guild_id, interaction.user.id)

        embed = discord.Embed(title="🎫 Мои тикеты", color=0x5865F2)
        if not tickets:
            embed.description = "У тебя нет тикетов."
        else:
            for t in tickets:
                status_emoji = "🟢" if t["status"] == "open" else "🔴"
                type_label = TICKET_TYPES.get(t["ticket_type"], t["ticket_type"])
                channel_str = f"<#{t['channel_id']}>" if t["channel_id"] else "—"
                embed.add_field(
                    name=f"{status_emoji} #{t['id']} — {type_label}",
                    value=f"Канал: {channel_str} | Статус: {t['status']}",
                    inline=False
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /admin-deduct (списать bloodstone после выплаты) ─────────────────────

    @app_commands.command(name="admin-deduct", description="[Админ] Списать Bloodstone после выплаты")
    @app_commands.describe(member="Пользователь", amount="Сколько Bloodstone списать")
    @app_commands.default_permissions(administrator=True)
    async def admin_deduct(self, interaction: discord.Interaction,
                           member: discord.Member, amount: int):
        # Bloodstone уже заморожен (снят при создании тикета), просто подтверждение
        embed = discord.Embed(
            title="✅ Выплата подтверждена",
            description=f"{member.mention}: **{amount:,} Bloodstone** списано после выплаты.",
            color=0x57F287
        )
        await interaction.response.send_message(embed=embed)

    # ── /admin-return (вернуть bloodstone если отказали) ─────────────────────

    @app_commands.command(name="admin-return", description="[Админ] Вернуть Bloodstone (отказ в выплате)")
    @app_commands.describe(member="Пользователь", amount="Сколько вернуть")
    @app_commands.default_permissions(administrator=True)
    async def admin_return(self, interaction: discord.Interaction,
                           member: discord.Member, amount: int):
        self.bot.db.ensure_user(interaction.guild_id, member.id)
        self.bot.db.add_bloodstone(interaction.guild_id, member.id, amount)
        embed = discord.Embed(
            title="↩️ Bloodstone возвращён",
            description=f"{member.mention} получил обратно **{amount:,} Bloodstone**.",
            color=0xFEE75C
        )
        await interaction.response.send_message(embed=embed)

    # ── /withdrawal-info ──────────────────────────────────────────────────────

    @app_commands.command(name="withdrawal-info", description="Как вывести деньги за инвайты?")
    async def withdrawal_info(self, interaction: discord.Interaction):
        db = self.bot.db
        config = db.get_config(interaction.guild_id)
        rate = config["bloodstone_rate"] if config else 0.01
        min_w = config["min_withdrawal"] if config else 500

        db.ensure_user(interaction.guild_id, interaction.user.id)
        user_data = db.get_user(interaction.guild_id, interaction.user.id)
        bs = user_data["bloodstone"]
        usd = round(bs * rate, 2)

        embed = discord.Embed(
            title="💸 Система вывода средств",
            color=0x1a1a2e
        )
        embed.add_field(
            name="💎 Что такое Bloodstone?",
            value=(
                "Bloodstone — особая валюта, которую ты зарабатываешь **приглашая друзей** на сервер.\n"
                "За каждый активный инвайт ты получаешь **+10 Bloodstone**."
            ),
            inline=False
        )
        embed.add_field(
            name="📈 Курс",
            value=f"**1 Bloodstone = ${rate}**\n(Пример: 500 BS = ${round(500 * rate, 2)})",
            inline=True
        )
        embed.add_field(
            name="📊 Твой баланс",
            value=f"**{bs:,} Bloodstone** (~${usd})",
            inline=True
        )
        embed.add_field(
            name="💳 Минимум для вывода",
            value=f"**{min_w:,} Bloodstone** (~${round(min_w * rate, 2)})",
            inline=True
        )
        embed.add_field(
            name="🚀 Как вывести?",
            value=(
                "1. Накопи минимум для вывода\n"
                "2. Напиши `/ticket withdrawal` и укажи реквизиты\n"
                "3. Дождись ответа администратора\n"
                "4. Получи оплату"
            ),
            inline=False
        )
        embed.add_field(
            name="✅ Доступные способы оплаты",
            value="Binance Pay · USDT (TRC20/ERC20) · PayPal · Карта (по договорённости)",
            inline=False
        )
        embed.set_footer(text="Приглашай друзей и зарабатывай реальные деньги!")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))

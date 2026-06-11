import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from database import get_rank_name, get_rank_emoji

ROLES_MAP = {
    "carry":    ("🗡️ Carry",     "role_carry"),
    "mid":      ("🔮 Mid",       "role_mid"),
    "offlane":  ("🛡️ Offlane",  "role_offlane"),
    "support":  ("💚 Support",   "role_support"),
    "hardsup":  ("🔧 Hard Sup", "role_hardsup"),
}


class ProfileCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /profile ─────────────────────────────────────────────────────────────

    @app_commands.command(name="profile", description="Твой Dota 2 профиль на сервере")
    @app_commands.describe(member="Участник (по умолчанию — ты)")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        db = self.bot.db
        db.ensure_user(interaction.guild_id, target.id)
        d = db.get_user(interaction.guild_id, target.id)
        invites = db.get_invite_count(interaction.guild_id, target.id)

        rank_name = get_rank_name(d["rank_tier"])
        rank_emoji = get_rank_emoji(d["rank_tier"])
        xp_needed = d["level"] * 100 + 100
        filled = int((d["xp"] / xp_needed) * 10)
        bar = "█" * filled + "░" * (10 - filled)

        roles_list = []
        for key, (label, col) in ROLES_MAP.items():
            if d[col]:
                roles_list.append(label)

        config = db.get_config(interaction.guild_id)
        rate = config["bloodstone_rate"] if config else 0.01
        usd_value = round(d["bloodstone"] * rate, 2)

        embed = discord.Embed(
            title=f"{rank_emoji} {target.display_name}",
            color=0x1a1a2e
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        # Dota stats
        mmr_str = f"{d['mmr']:,} MMR" if d["mmr"] > 0 else "Не указан"
        steam_str = f"[Steam](https://steamcommunity.com/profiles/{d['steam_id']})" if d["steam_id"] else "Не привязан"
        embed.add_field(name="🎮 Ранг", value=f"{rank_emoji} {rank_name}", inline=True)
        embed.add_field(name="📊 MMR", value=mmr_str, inline=True)
        embed.add_field(name="🔗 Steam", value=steam_str, inline=True)

        roles_str = " ".join(roles_list) if roles_list else "Не указаны"
        embed.add_field(name="🎯 Роли", value=roles_str, inline=False)

        lang = d["language"] or "RU"
        lfg_status = "✅ Ищет команду" if d["looking_for_team"] else "❌ Не ищет"
        embed.add_field(name="🌐 Язык", value=lang, inline=True)
        embed.add_field(name="🔍 LFG", value=lfg_status, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Economy
        embed.add_field(name="⭐ Уровень", value=str(d["level"]), inline=True)
        embed.add_field(name="🏆 Aegis", value=f"{d['aegis']:,}", inline=True)
        embed.add_field(name="💎 Bloodstone", value=f"{d['bloodstone']:,} (~${usd_value})", inline=True)
        embed.add_field(name=f"XP {d['xp']}/{xp_needed}", value=f"`[{bar}]`", inline=False)

        # Invites
        embed.add_field(
            name="🔗 Инвайты",
            value=f"Всего: **{invites['total']}** | Активны: **{invites['active']}** | Ушли: **{invites['left']}**",
            inline=False
        )

        if d["lfg_description"]:
            embed.add_field(name="📝 О себе", value=d["lfg_description"], inline=False)

        embed.set_footer(text="Dota 2 Community Server")
        await interaction.response.send_message(embed=embed)

    # ── /setup-profile ────────────────────────────────────────────────────────

    @app_commands.command(name="setup-profile", description="Настроить свой Dota 2 профиль")
    @app_commands.describe(
        mmr="Твой MMR",
        language="Язык общения (RU/EN/UA и т.д.)",
        description="О себе (для поиска команды)",
        steam_id="SteamID64 (17 цифр)"
    )
    async def setup_profile(self, interaction: discord.Interaction,
                             mmr: int = None, language: str = None,
                             description: str = None, steam_id: str = None):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)
        updates = {}

        if mmr is not None:
            if mmr < 0 or mmr > 15000:
                await interaction.response.send_message("❌ MMR должен быть от 0 до 15000.", ephemeral=True)
                return
            updates["mmr"] = mmr
            # Автоматически ставим ранг по MMR
            if mmr < 770: updates["rank_tier"] = 10
            elif mmr < 1540: updates["rank_tier"] = 20
            elif mmr < 2310: updates["rank_tier"] = 30
            elif mmr < 3080: updates["rank_tier"] = 40
            elif mmr < 3850: updates["rank_tier"] = 50
            elif mmr < 4620: updates["rank_tier"] = 60
            elif mmr < 5420: updates["rank_tier"] = 70
            else: updates["rank_tier"] = 80

        if language:
            updates["language"] = language.upper()[:5]
        if description:
            updates["lfg_description"] = description[:300]
        if steam_id:
            if not steam_id.isdigit() or len(steam_id) != 17:
                await interaction.response.send_message(
                    "❌ SteamID64 должен состоять из 17 цифр.\n"
                    "Найти его можно на [steamid.io](https://steamid.io/)", ephemeral=True
                )
                return
            updates["steam_id"] = steam_id

        if not updates:
            await interaction.response.send_message(
                "ℹ️ Укажи хотя бы один параметр. Доступно: `mmr`, `language`, `description`, `steam_id`",
                ephemeral=True
            )
            return

        db.update_user(interaction.guild_id, interaction.user.id, **updates)
        await interaction.response.send_message(
            f"✅ Профиль обновлён! Используй `/profile` чтобы посмотреть.", ephemeral=True
        )

    # ── /set-roles ────────────────────────────────────────────────────────────

    @app_commands.command(name="set-roles", description="Выбрать свои роли в Dota 2")
    @app_commands.describe(
        carry="Играешь керри?", mid="Играешь мид?", offlane="Играешь офлайн?",
        support="Играешь саппорт?", hardsup="Играешь 5-ю позицию?"
    )
    async def set_roles(self, interaction: discord.Interaction,
                        carry: bool = False, mid: bool = False,
                        offlane: bool = False, support: bool = False, hardsup: bool = False):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)
        db.update_user(interaction.guild_id, interaction.user.id,
                       role_carry=int(carry), role_mid=int(mid),
                       role_offlane=int(offlane), role_support=int(support),
                       role_hardsup=int(hardsup))

        active = []
        if carry: active.append("🗡️ Carry")
        if mid: active.append("🔮 Mid")
        if offlane: active.append("🛡️ Offlane")
        if support: active.append("💚 Support")
        if hardsup: active.append("🔧 Hard Support")

        embed = discord.Embed(
            title="✅ Роли обновлены",
            description=" ".join(active) if active else "Все роли сняты",
            color=0x1a1a2e
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /lfg ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="lfg", description="Включить/выключить поиск команды")
    @app_commands.describe(enable="Включить поиск", description="Расскажи о себе")
    async def lfg(self, interaction: discord.Interaction, enable: bool = True, description: str = None):
        db = self.bot.db
        db.ensure_user(interaction.guild_id, interaction.user.id)
        updates = {"looking_for_team": int(enable)}
        if description:
            updates["lfg_description"] = description[:300]
        db.update_user(interaction.guild_id, interaction.user.id, **updates)

        if enable:
            embed = discord.Embed(
                title="🔍 Ты в поиске команды!",
                description="Тебя увидят в `/find-players`. Убрать: `/lfg enable:False`",
                color=0x57F287
            )
        else:
            embed = discord.Embed(title="✅ Поиск команды отключён", color=0xED4245)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /find-players ─────────────────────────────────────────────────────────

    @app_commands.command(name="find-players", description="Найти игроков для команды")
    @app_commands.describe(
        min_mmr="Минимальный MMR", max_mmr="Максимальный MMR",
        role="Нужная роль", language="Язык"
    )
    @app_commands.choices(role=[
        app_commands.Choice(name="🗡️ Carry", value="carry"),
        app_commands.Choice(name="🔮 Mid", value="mid"),
        app_commands.Choice(name="🛡️ Offlane", value="offlane"),
        app_commands.Choice(name="💚 Support", value="support"),
        app_commands.Choice(name="🔧 Hard Support", value="hardsup"),
    ])
    async def find_players(self, interaction: discord.Interaction,
                           min_mmr: int = None, max_mmr: int = None,
                           role: str = None, language: str = None):
        db = self.bot.db
        filters = {}
        if min_mmr: filters["min_mmr"] = min_mmr
        if max_mmr: filters["max_mmr"] = max_mmr
        if role: filters["role"] = role
        if language: filters["language"] = language.upper()

        players = db.get_lfg_players(interaction.guild_id, filters)

        embed = discord.Embed(title="🔍 Игроки в поиске команды", color=0x1a1a2e)
        if not players:
            embed.description = "Никого не найдено по твоим фильтрам."
        else:
            for p in players[:10]:
                mmr_str = f"{p['mmr']:,} MMR" if p["mmr"] > 0 else "MMR не указан"
                rank_str = f"{get_rank_emoji(p['rank_tier'])} {get_rank_name(p['rank_tier'])}"
                roles_list = [label for key, (label, col) in ROLES_MAP.items() if p[col]]
                roles_str = " ".join(roles_list) if roles_list else "—"
                desc_str = f"\n> *{p['lfg_description'][:80]}*" if p["lfg_description"] else ""
                embed.add_field(
                    name=f"<@{p['user_id']}> — {rank_str}",
                    value=f"{mmr_str} | {p['language']} | {roles_str}{desc_str}",
                    inline=False
                )
        await interaction.response.send_message(embed=embed)

    # ── /dota-stats ───────────────────────────────────────────────────────────

    @app_commands.command(name="dota-stats", description="Статистика из OpenDota по Steam ID")
    @app_commands.describe(member="Участник (по умолчанию — ты)")
    async def dota_stats(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        db = self.bot.db
        db.ensure_user(interaction.guild_id, target.id)
        user_data = db.get_user(interaction.guild_id, target.id)

        if not user_data["steam_id"]:
            await interaction.response.send_message(
                "❌ Steam не привязан. Используй `/setup-profile steam_id:ТвойSteamID64`",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        account_id = int(user_data["steam_id"]) - 76561197960265728

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.opendota.com/api/players/{account_id}") as r:
                    player = await r.json()
                async with session.get(f"https://api.opendota.com/api/players/{account_id}/wl") as r:
                    wl = await r.json()
                async with session.get(f"https://api.opendota.com/api/players/{account_id}/heroes?limit=3") as r:
                    heroes_raw = await r.json()

            profile = player.get("profile", {})
            mmr = player.get("mmr_estimate", {}).get("estimate", 0)
            wins = wl.get("win", 0)
            losses = wl.get("lose", 0)
            total = wins + losses
            winrate = round(wins / total * 100, 1) if total > 0 else 0

            embed = discord.Embed(
                title=f"🎮 Dota 2 статистика — {profile.get('personaname', target.display_name)}",
                color=0x1a1a2e
            )
            avatar = profile.get("avatarfull", "")
            if avatar:
                embed.set_thumbnail(url=avatar)

            embed.add_field(name="📊 MMR (оценка)", value=f"{mmr:,}" if mmr else "Приватный", inline=True)
            embed.add_field(name="🏆 Победы", value=str(wins), inline=True)
            embed.add_field(name="💀 Поражения", value=str(losses), inline=True)
            embed.add_field(name="📈 Винрейт", value=f"{winrate}%", inline=True)
            embed.add_field(name="🎯 Игр всего", value=str(total), inline=True)

            country = profile.get("loccountrycode", "—")
            embed.add_field(name="🌍 Страна", value=country, inline=True)

            if heroes_raw and isinstance(heroes_raw, list):
                top_heroes = heroes_raw[:3]
                heroes_str = "\n".join(
                    f"Hero #{h.get('hero_id', '?')}: {h.get('games', 0)} игр, {h.get('win', 0)} побед"
                    for h in top_heroes
                )
                embed.add_field(name="🦸 Топ герои", value=heroes_str or "—", inline=False)

            embed.set_footer(text="Данные: OpenDota API")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка при получении данных: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ProfileCog(bot))

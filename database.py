import sqlite3
import time
import threading
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    xp              INTEGER DEFAULT 0,
    level           INTEGER DEFAULT 1,
    aegis           INTEGER DEFAULT 0,  -- внутренняя валюта
    bloodstone      INTEGER DEFAULT 0,  -- очки за инвайты (конвертируются в реальные деньги)
    last_xp         INTEGER DEFAULT 0,
    last_daily      INTEGER DEFAULT 0,
    daily_streak    INTEGER DEFAULT 0,
    steam_id        TEXT DEFAULT NULL,
    mmr             INTEGER DEFAULT 0,
    rank_tier       INTEGER DEFAULT 0,  -- 10=Herald ... 80=Immortal
    role_carry      INTEGER DEFAULT 0,
    role_mid        INTEGER DEFAULT 0,
    role_offlane    INTEGER DEFAULT 0,
    role_support    INTEGER DEFAULT 0,
    role_hardsup    INTEGER DEFAULT 0,
    language        TEXT DEFAULT 'RU',
    looking_for_team INTEGER DEFAULT 0,
    lfg_description TEXT DEFAULT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS invites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    inviter_id      INTEGER NOT NULL,
    invited_id      INTEGER NOT NULL,
    invite_code     TEXT,
    joined_at       INTEGER DEFAULT (strftime('%s','now')),
    left_at         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tournaments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    ticket_price    INTEGER NOT NULL,   -- в Aegis
    prize_pool      TEXT DEFAULT '0',
    max_teams       INTEGER DEFAULT 8,
    status          TEXT DEFAULT 'open', -- open/closed/finished
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS tournament_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    team_name       TEXT,
    registered_at   INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS shop_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    role_id         INTEGER DEFAULT NULL,
    price           INTEGER NOT NULL,
    item_type       TEXT DEFAULT 'role'  -- role / cosmetic
);

CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    channel_id      INTEGER DEFAULT NULL,
    ticket_type     TEXT NOT NULL,   -- withdrawal / support / report
    status          TEXT DEFAULT 'open',  -- open/closed
    amount          INTEGER DEFAULT 0,   -- для вывода: сколько bloodstone
    details         TEXT,
    created_at      INTEGER DEFAULT (strftime('%s','now')),
    closed_at       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL UNIQUE,
    captain_id      INTEGER NOT NULL,
    description     TEXT,
    min_mmr         INTEGER DEFAULT 0,
    created_at      INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS team_members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id         INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    joined_at       INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(team_id, user_id)
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id        INTEGER PRIMARY KEY,
    ticket_category INTEGER DEFAULT NULL,  -- ID категории для тикетов
    announce_channel INTEGER DEFAULT NULL,
    admin_role      INTEGER DEFAULT NULL,
    bloodstone_rate REAL DEFAULT 0.01,     -- 1 bloodstone = 0.01 USD
    min_withdrawal  INTEGER DEFAULT 500    -- минимум для вывода
);
"""

RANK_NAMES = {
    0: "Unranked", 10: "Herald", 20: "Guardian", 30: "Crusader",
    40: "Archon", 50: "Legend", 60: "Ancient", 70: "Divine", 80: "Immortal"
}

RANK_EMOJIS = {
    0: "⬜", 10: "🟫", 20: "🟫", 30: "⚪", 40: "🟢",
    50: "🔵", 60: "🟣", 70: "🟡", 80: "🔴"
}

def get_rank_name(tier: int) -> str:
    base = (tier // 10) * 10
    star = tier % 10
    name = RANK_NAMES.get(base, "Unranked")
    if star > 0 and base < 80:
        return f"{name} {'★' * star}"
    return name

def get_rank_emoji(tier: int) -> str:
    base = (tier // 10) * 10
    return RANK_EMOJIS.get(base, "⬜")


class Database:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            self._local.conn = conn
        return self._local.conn

    @property
    def conn(self):
        return self._get_conn()

    # ── Пользователи ──────────────────────────────────────────────────────────

    def ensure_user(self, guild_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?,?)",
            (guild_id, user_id)
        )
        self.conn.commit()

    def get_user(self, guild_id: int, user_id: int):
        return self.conn.execute(
            "SELECT * FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ).fetchone()

    def update_user(self, guild_id: int, user_id: int, **kwargs):
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [guild_id, user_id]
        self.conn.execute(
            f"UPDATE users SET {sets} WHERE guild_id=? AND user_id=?", vals
        )
        self.conn.commit()

    def add_aegis(self, guild_id: int, user_id: int, amount: int):
        self.conn.execute(
            "UPDATE users SET aegis=MAX(0, aegis+?) WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id)
        )
        self.conn.commit()

    def add_bloodstone(self, guild_id: int, user_id: int, amount: int):
        self.conn.execute(
            "UPDATE users SET bloodstone=MAX(0, bloodstone+?) WHERE guild_id=? AND user_id=?",
            (amount, guild_id, user_id)
        )
        self.conn.commit()

    def process_message(self, guild_id: int, user_id: int):
        now = int(time.time())
        row = self.conn.execute(
            "SELECT xp, level, last_xp FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ).fetchone()
        if not row or now - row["last_xp"] < 60:
            return None
        xp_gain, aegis_gain = 15, 3
        new_xp = row["xp"] + xp_gain
        old_level = row["level"]
        new_level = self._calc_level(new_xp)
        leveled_up = new_level > old_level
        self.conn.execute(
            "UPDATE users SET xp=?, level=?, aegis=aegis+?, last_xp=? WHERE guild_id=? AND user_id=?",
            (new_xp, new_level, aegis_gain, now, guild_id, user_id)
        )
        self.conn.commit()
        return (xp_gain, aegis_gain, leveled_up, new_level)

    def _calc_level(self, xp: int) -> int:
        level = 1
        while xp >= level * 100 + 100:
            xp -= level * 100 + 100
            level += 1
        return level

    def claim_daily(self, guild_id: int, user_id: int) -> dict:
        now = int(time.time())
        row = self.conn.execute(
            "SELECT last_daily, daily_streak FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ).fetchone()
        if not row:
            return {"success": False, "seconds_left": 86400}
        diff = now - row["last_daily"]
        if diff < 86400:
            return {"success": False, "seconds_left": 86400 - diff}
        streak = 0 if diff > 172800 else row["daily_streak"]
        streak += 1
        reward = 50 * min(streak, 7)
        self.conn.execute(
            "UPDATE users SET aegis=aegis+?, last_daily=?, daily_streak=? WHERE guild_id=? AND user_id=?",
            (reward, now, streak, guild_id, user_id)
        )
        self.conn.commit()
        return {"success": True, "reward": reward, "streak": streak}

    # ── Инвайты ───────────────────────────────────────────────────────────────

    def add_invite(self, guild_id: int, inviter_id: int, invited_id: int, code: str):
        self.conn.execute(
            "INSERT INTO invites (guild_id, inviter_id, invited_id, invite_code) VALUES (?,?,?,?)",
            (guild_id, inviter_id, invited_id, code)
        )
        # +50 Aegis и +10 Bloodstone за каждый инвайт
        self.conn.execute(
            "UPDATE users SET aegis=aegis+50, bloodstone=bloodstone+10 WHERE guild_id=? AND user_id=?",
            (guild_id, inviter_id)
        )
        self.conn.commit()

    def member_left(self, guild_id: int, user_id: int):
        now = int(time.time())
        self.conn.execute(
            "UPDATE invites SET left_at=? WHERE guild_id=? AND invited_id=? AND left_at=0",
            (now, guild_id, user_id)
        )
        self.conn.commit()

    def get_invite_count(self, guild_id: int, inviter_id: int) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM invites WHERE guild_id=? AND inviter_id=?",
            (guild_id, inviter_id)
        ).fetchone()[0]
        left = self.conn.execute(
            "SELECT COUNT(*) FROM invites WHERE guild_id=? AND inviter_id=? AND left_at>0",
            (guild_id, inviter_id)
        ).fetchone()[0]
        return {"total": total, "active": total - left, "left": left}

    def get_invited_list(self, guild_id: int, inviter_id: int) -> list:
        return self.conn.execute(
            "SELECT invited_id, left_at FROM invites WHERE guild_id=? AND inviter_id=? ORDER BY joined_at DESC",
            (guild_id, inviter_id)
        ).fetchall()

    # ── Лидерборды ────────────────────────────────────────────────────────────

    def get_leaderboard(self, guild_id: int, by: str, limit: int = 10) -> list:
        if by == "xp":
            return self.conn.execute(
                "SELECT user_id, xp, level FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT ?",
                (guild_id, limit)
            ).fetchall()
        elif by == "aegis":
            return self.conn.execute(
                "SELECT user_id, aegis FROM users WHERE guild_id=? ORDER BY aegis DESC LIMIT ?",
                (guild_id, limit)
            ).fetchall()
        elif by == "mmr":
            return self.conn.execute(
                "SELECT user_id, mmr, rank_tier FROM users WHERE guild_id=? AND mmr>0 ORDER BY mmr DESC LIMIT ?",
                (guild_id, limit)
            ).fetchall()
        elif by == "invites":
            return self.conn.execute(
                """SELECT inviter_id AS user_id, COUNT(*) AS count
                   FROM invites WHERE guild_id=? AND left_at=0
                   GROUP BY inviter_id ORDER BY count DESC LIMIT ?""",
                (guild_id, limit)
            ).fetchall()
        return []

    # ── Турниры ───────────────────────────────────────────────────────────────

    def create_tournament(self, guild_id: int, name: str, description: str,
                          ticket_price: int, prize_pool: str, max_teams: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO tournaments (guild_id, name, description, ticket_price, prize_pool, max_teams) VALUES (?,?,?,?,?,?)",
            (guild_id, name, description, ticket_price, prize_pool, max_teams)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_tournaments(self, guild_id: int, status: str = "open") -> list:
        return self.conn.execute(
            "SELECT * FROM tournaments WHERE guild_id=? AND status=? ORDER BY created_at DESC",
            (guild_id, status)
        ).fetchall()

    def get_tournament(self, tournament_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()

    def register_tournament(self, tournament_id: int, guild_id: int, user_id: int, team_name: str) -> dict:
        t = self.get_tournament(tournament_id)
        if not t:
            return {"success": False, "error": "Турнир не найден"}
        if t["status"] != "open":
            return {"success": False, "error": "Регистрация закрыта"}

        already = self.conn.execute(
            "SELECT id FROM tournament_registrations WHERE tournament_id=? AND user_id=?",
            (tournament_id, user_id)
        ).fetchone()
        if already:
            return {"success": False, "error": "Вы уже зарегистрированы"}

        user = self.get_user(guild_id, user_id)
        if not user or user["aegis"] < t["ticket_price"]:
            return {"success": False, "error": f"Недостаточно Aegis. Нужно: {t['ticket_price']}"}

        self.conn.execute(
            "UPDATE users SET aegis=aegis-? WHERE guild_id=? AND user_id=?",
            (t["ticket_price"], guild_id, user_id)
        )
        self.conn.execute(
            "INSERT INTO tournament_registrations (tournament_id, user_id, guild_id, team_name) VALUES (?,?,?,?)",
            (tournament_id, user_id, guild_id, team_name)
        )
        self.conn.commit()
        return {"success": True}

    def get_tournament_participants(self, tournament_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM tournament_registrations WHERE tournament_id=? ORDER BY registered_at",
            (tournament_id,)
        ).fetchall()

    def update_tournament_status(self, tournament_id: int, status: str):
        self.conn.execute("UPDATE tournaments SET status=? WHERE id=?", (status, tournament_id))
        self.conn.commit()

    # ── Тикеты ────────────────────────────────────────────────────────────────

    def create_ticket(self, guild_id: int, user_id: int, ticket_type: str,
                      amount: int = 0, details: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO tickets (guild_id, user_id, ticket_type, amount, details) VALUES (?,?,?,?,?)",
            (guild_id, user_id, ticket_type, amount, details)
        )
        self.conn.commit()
        return cur.lastrowid

    def update_ticket_channel(self, ticket_id: int, channel_id: int):
        self.conn.execute("UPDATE tickets SET channel_id=? WHERE id=?", (channel_id, ticket_id))
        self.conn.commit()

    def close_ticket(self, ticket_id: int):
        now = int(time.time())
        self.conn.execute(
            "UPDATE tickets SET status='closed', closed_at=? WHERE id=?", (now, ticket_id)
        )
        self.conn.commit()

    def get_open_tickets(self, guild_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND status='open' ORDER BY created_at",
            (guild_id,)
        ).fetchall()

    def get_ticket_by_channel(self, channel_id: int):
        return self.conn.execute(
            "SELECT * FROM tickets WHERE channel_id=?", (channel_id,)
        ).fetchone()

    def get_user_tickets(self, guild_id: int, user_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT 10",
            (guild_id, user_id)
        ).fetchall()

    # ── Команды ───────────────────────────────────────────────────────────────

    def create_team(self, guild_id: int, captain_id: int, name: str,
                    description: str, min_mmr: int) -> dict:
        existing = self.conn.execute("SELECT id FROM teams WHERE guild_id=? AND name=?",
                                     (guild_id, name)).fetchone()
        if existing:
            return {"success": False, "error": "Команда с таким названием уже существует"}
        cur = self.conn.execute(
            "INSERT INTO teams (guild_id, captain_id, name, description, min_mmr) VALUES (?,?,?,?,?)",
            (guild_id, captain_id, name, description, min_mmr)
        )
        team_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO team_members (team_id, user_id) VALUES (?,?)", (team_id, captain_id)
        )
        self.conn.commit()
        return {"success": True, "team_id": team_id}

    def get_teams(self, guild_id: int) -> list:
        return self.conn.execute(
            """SELECT t.*, COUNT(tm.id) as member_count 
               FROM teams t LEFT JOIN team_members tm ON t.id=tm.team_id
               WHERE t.guild_id=? GROUP BY t.id ORDER BY t.created_at DESC""",
            (guild_id,)
        ).fetchall()

    def get_team(self, team_id: int):
        return self.conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()

    def get_team_members(self, team_id: int) -> list:
        return self.conn.execute(
            "SELECT user_id FROM team_members WHERE team_id=?", (team_id,)
        ).fetchall()

    def join_team(self, team_id: int, user_id: int) -> dict:
        count = self.conn.execute(
            "SELECT COUNT(*) FROM team_members WHERE team_id=?", (team_id,)
        ).fetchone()[0]
        if count >= 5:
            return {"success": False, "error": "Команда уже заполнена (5/5)"}
        existing = self.conn.execute(
            "SELECT id FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id)
        ).fetchone()
        if existing:
            return {"success": False, "error": "Вы уже в этой команде"}
        self.conn.execute(
            "INSERT INTO team_members (team_id, user_id) VALUES (?,?)", (team_id, user_id)
        )
        self.conn.commit()
        return {"success": True}

    def leave_team(self, team_id: int, user_id: int):
        self.conn.execute(
            "DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id)
        )
        self.conn.commit()

    def delete_team(self, team_id: int):
        self.conn.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
        self.conn.execute("DELETE FROM teams WHERE id=?", (team_id,))
        self.conn.commit()

    def get_user_team(self, guild_id: int, user_id: int):
        return self.conn.execute(
            """SELECT t.* FROM teams t 
               JOIN team_members tm ON t.id=tm.team_id
               WHERE t.guild_id=? AND tm.user_id=?""",
            (guild_id, user_id)
        ).fetchone()

    # ── LFG (поиск команды) ───────────────────────────────────────────────────

    def get_lfg_players(self, guild_id: int, filters: dict = None) -> list:
        query = "SELECT * FROM users WHERE guild_id=? AND looking_for_team=1"
        params = [guild_id]
        if filters:
            if filters.get("min_mmr"):
                query += " AND mmr>=?"
                params.append(filters["min_mmr"])
            if filters.get("max_mmr"):
                query += " AND mmr<=?"
                params.append(filters["max_mmr"])
            if filters.get("role"):
                query += f" AND role_{filters['role']}=1"
            if filters.get("language"):
                query += " AND language=?"
                params.append(filters["language"])
        query += " ORDER BY mmr DESC LIMIT 20"
        return self.conn.execute(query, params).fetchall()

    # ── Магазин ───────────────────────────────────────────────────────────────

    def add_shop_item(self, guild_id: int, name: str, description: str,
                      role_id: int, price: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO shop_items (guild_id, name, description, role_id, price) VALUES (?,?,?,?,?)",
            (guild_id, name, description, role_id, price)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_shop_items(self, guild_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM shop_items WHERE guild_id=?", (guild_id,)
        ).fetchall()

    def get_shop_item(self, item_id: int):
        return self.conn.execute("SELECT * FROM shop_items WHERE id=?", (item_id,)).fetchone()

    def remove_shop_item(self, item_id: int):
        self.conn.execute("DELETE FROM shop_items WHERE id=?", (item_id,))
        self.conn.commit()

    # ── Конфиг сервера ────────────────────────────────────────────────────────

    def get_config(self, guild_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,)
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)
        ).fetchone()

    def set_config(self, guild_id: int, **kwargs):
        self.conn.execute(
            "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,)
        )
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [guild_id]
        self.conn.execute(f"UPDATE guild_config SET {sets} WHERE guild_id=?", vals)
        self.conn.commit()

import os
import random
from datetime import datetime, timezone

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DATABASE_PATH", "giveaways.db")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def db_execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()


async def db_fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return await cur.fetchone()


async def db_fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return await cur.fetchall()


async def setup_database():
    await db_execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            message_id INTEGER, title TEXT NOT NULL, description TEXT,
            prize TEXT NOT NULL, image_url TEXT, goal_type TEXT NOT NULL,
            goal_value INTEGER NOT NULL, winner_count INTEGER NOT NULL,
            required_role_id INTEGER, bonus_role_id INTEGER,
            bonus_entries INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
            created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
            ended_at TEXT, winners TEXT
        )
    """)
    await db_execute("""
        CREATE TABLE IF NOT EXISTS entries (
            giveaway_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            entries INTEGER DEFAULT 1, joined_at TEXT NOT NULL,
            PRIMARY KEY (giveaway_id, user_id)
        )
    """)


def build_embed(g, participant_count, guild=None):
    goal_label = "membros no servidor" if g["goal_type"] == "members" else "participantes"
    current = guild.member_count if guild and g["goal_type"] == "members" else participant_count
    color = discord.Color.green() if g["status"] == "active" else discord.Color.dark_grey()
    embed = discord.Embed(title=f"🎉 {g['title']}", description=g["description"] or "", color=color)
    embed.add_field(name="🎁 Prêmio", value=g["prize"], inline=False)
    embed.add_field(name="🎯 Meta", value=f"{current}/{g['goal_value']} {goal_label}", inline=False)
    embed.add_field(name="🏆 Ganhadores", value=str(g["winner_count"]), inline=True)
    embed.add_field(name="👥 Participantes", value=str(participant_count), inline=True)
    rules = []
    if g["required_role_id"]:
        rules.append(f"Cargo obrigatório: <@&{g['required_role_id']}>")
    if g["bonus_role_id"] and g["bonus_entries"]:
        rules.append(f"<@&{g['bonus_role_id']}> recebe +{g['bonus_entries']} entrada(s)")
    if rules:
        embed.add_field(name="📌 Requisitos", value="\n".join(rules), inline=False)
    if g["image_url"]:
        embed.set_thumbnail(url=g["image_url"])
    if g["status"] == "ended":
        winners = g["winners"] or "Nenhum participante elegível"
        embed.add_field(name="Vencedores", value=winners, inline=False)
        embed.set_footer(text=f"Terminado em {g['ended_at'] or '-'}")
    else:
        embed.set_footer(text="Clique em 🎉 Participar para entrar gratuitamente!")
    return embed


async def refresh_message(giveaway_id):
    g = await db_fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
    if not g or not g["message_id"]:
        return
    guild = bot.get_guild(g["guild_id"])
    channel = bot.get_channel(g["channel_id"])
    if not guild or not channel:
        return
    count = (await db_fetchone("SELECT COUNT(*) AS n FROM entries WHERE giveaway_id = ?", (giveaway_id,)))["n"]
    try:
        message = await channel.fetch_message(g["message_id"])
        await message.edit(embed=build_embed(g, count, guild), view=GiveawayView(giveaway_id, g["status"] == "active"))
    except (discord.NotFound, discord.Forbidden):
        pass


async def finish_giveaway(giveaway_id):
    g = await db_fetchone("SELECT * FROM giveaways WHERE id = ? AND status = 'active'", (giveaway_id,))
    if not g:
        return False
    entries = await db_fetchall("SELECT user_id, entries FROM entries WHERE giveaway_id = ?", (giveaway_id,))
    pool = []
    for entry in entries:
        pool.extend([entry["user_id"]] * max(1, entry["entries"]))
    winners = []
    while pool and len(winners) < g["winner_count"]:
        winner = random.choice(pool)
        if winner not in winners:
            winners.append(winner)
        pool = [uid for uid in pool if uid != winner]
    winner_text = ", ".join(f"<@{uid}>" for uid in winners) or "Nenhum participante elegível"
    ended = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    await db_execute("UPDATE giveaways SET status='ended', ended_at=?, winners=? WHERE id=?", (ended, winner_text, giveaway_id))
    await refresh_message(giveaway_id)
    channel = bot.get_channel(g["channel_id"])
    if channel:
        await channel.send(f"🏆 **Sorteio encerrado!**\nPrêmio: **{g['prize']}**\nVencedor(es): {winner_text}")
    return True


async def goal_reached(g):
    if g["goal_type"] == "members":
        guild = bot.get_guild(g["guild_id"])
        return bool(guild and guild.member_count >= g["goal_value"])
    count = (await db_fetchone("SELECT COUNT(*) AS n FROM entries WHERE giveaway_id=?", (g["id"],)))["n"]
    return count >= g["goal_value"]


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id, active=True):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        join = discord.ui.Button(label="Participar", emoji="🎉", style=discord.ButtonStyle.blurple,
                                 custom_id=f"giveaway:join:{giveaway_id}", disabled=not active)
        people = discord.ui.Button(label="Participantes", emoji="👥", style=discord.ButtonStyle.secondary,
                                   custom_id=f"giveaway:people:{giveaway_id}")
        join.callback = self.join_callback
        people.callback = self.people_callback
        self.add_item(join)
        self.add_item(people)

    async def join_callback(self, interaction: discord.Interaction):
        g = await db_fetchone("SELECT * FROM giveaways WHERE id=?", (self.giveaway_id,))
        if not g or g["status"] != "active":
            return await interaction.response.send_message("Este sorteio já terminou.", ephemeral=True)
        member = interaction.user
        if g["required_role_id"] and not member.get_role(g["required_role_id"]):
            return await interaction.response.send_message(f"Você precisa do cargo <@&{g['required_role_id']}> para participar.", ephemeral=True)
        exists = await db_fetchone("SELECT 1 FROM entries WHERE giveaway_id=? AND user_id=?", (self.giveaway_id, member.id))
        if exists:
            return await interaction.response.send_message("Você já está participando deste sorteio! 🎉", ephemeral=True)
        chances = 1 + (g["bonus_entries"] if g["bonus_role_id"] and member.get_role(g["bonus_role_id"]) else 0)
        await db_execute("INSERT INTO entries VALUES (?, ?, ?, ?)", (self.giveaway_id, member.id, chances, datetime.now(timezone.utc).isoformat()))
        await interaction.response.send_message(f"Você entrou no sorteio com **{chances} entrada(s)**! Boa sorte 🍀", ephemeral=True)
        await refresh_message(self.giveaway_id)
        g = await db_fetchone("SELECT * FROM giveaways WHERE id=?", (self.giveaway_id,))
        if await goal_reached(g):
            await finish_giveaway(self.giveaway_id)

    async def people_callback(self, interaction: discord.Interaction):
        rows = await db_fetchall("SELECT user_id, entries FROM entries WHERE giveaway_id=? ORDER BY joined_at", (self.giveaway_id,))
        if not rows:
            text = "Ainda não há participantes."
        else:
            shown = rows[:40]
            text = "\n".join(f"<@{r['user_id']}> — {r['entries']} entrada(s)" for r in shown)
            if len(rows) > 40:
                text += f"\n...e mais {len(rows) - 40}."
        await interaction.response.send_message(f"**Participantes ({len(rows)}):**\n{text}", ephemeral=True)


class GiveawayModal(discord.ui.Modal, title="Configurar sorteio"):
    name = discord.ui.TextInput(label="Nome do sorteio", max_length=100)
    prize = discord.ui.TextInput(label="Prêmio", max_length=100)
    description = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, required=False, max_length=1000)
    goal = discord.ui.TextInput(label="Valor da meta", placeholder="3000", max_length=8)
    winners = discord.ui.TextInput(label="Quantidade de ganhadores", placeholder="2", default="1", max_length=2)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction):
        try:
            goal, winners = int(self.goal.value), int(self.winners.value)
            if goal < 1 or winners < 1 or winners > 20:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("Use números válidos. A quantidade de ganhadores deve ser de 1 a 20.", ephemeral=True)
        self.panel.data.update(name=self.name.value, prize=self.prize.value, description=self.description.value,
                               goal=goal, winners=winners)
        await interaction.response.send_message("Configuração principal salva. Agora use **Configuração avançada** e depois **Publicar**.", ephemeral=True)


class AdvancedModal(discord.ui.Modal, title="Configuração avançada"):
    image = discord.ui.TextInput(label="URL da imagem (opcional)", required=False)
    required_role = discord.ui.TextInput(label="ID do cargo obrigatório (opcional)", required=False)
    bonus_role = discord.ui.TextInput(label="ID do cargo com entradas extras", required=False)
    bonus_entries = discord.ui.TextInput(label="Quantidade de entradas extras", default="0", required=False)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction):
        try:
            req = int(self.required_role.value) if self.required_role.value else None
            bonus = int(self.bonus_role.value) if self.bonus_role.value else None
            extra = int(self.bonus_entries.value or 0)
            if extra < 0 or extra > 100:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("Os IDs e a quantidade de entradas precisam ser números válidos.", ephemeral=True)
        self.panel.data.update(image=self.image.value or None, required_role=req, bonus_role=bonus, bonus_entries=extra)
        await interaction.response.send_message("Configuração avançada salva. Agora selecione o canal e publique.", ephemeral=True)


class AdminPanel(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.data = {"goal_type": "participants", "channel_id": None, "image": None,
                     "required_role": None, "bonus_role": None, "bonus_entries": 0}

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este painel pertence a outro administrador.", ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="Tipo da meta", options=[
        discord.SelectOption(label="Participantes do sorteio", value="participants", emoji="🎉"),
        discord.SelectOption(label="Membros no servidor", value="members", emoji="👥")])
    async def meta_select(self, interaction, select):
        self.data["goal_type"] = select.values[0]
        await interaction.response.send_message("Tipo de meta selecionado.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="Canal do sorteio")
    async def channel_select(self, interaction, select):
        self.data["channel_id"] = select.values[0].id
        await interaction.response.send_message(f"Canal selecionado: {select.values[0].mention}", ephemeral=True)

    @discord.ui.button(label="Configurar", emoji="⚙️", style=discord.ButtonStyle.primary)
    async def configure(self, interaction, button):
        await interaction.response.send_modal(GiveawayModal(self))

    @discord.ui.button(label="Avançado", emoji="🛠️", style=discord.ButtonStyle.secondary)
    async def advanced(self, interaction, button):
        await interaction.response.send_modal(AdvancedModal(self))

    @discord.ui.button(label="Publicar", emoji="🚀", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        required = {"name", "prize", "goal", "winners", "channel_id"}
        if not required.issubset(self.data) or not self.data["channel_id"]:
            return await interaction.response.send_message("Preencha a configuração principal e selecione o canal.", ephemeral=True)
        channel = interaction.guild.get_channel(self.data["channel_id"])
        if not channel:
            return await interaction.response.send_message("Não encontrei o canal selecionado.", ephemeral=True)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("""INSERT INTO giveaways
                (guild_id,channel_id,title,description,prize,image_url,goal_type,goal_value,winner_count,
                 required_role_id,bonus_role_id,bonus_entries,created_by,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (interaction.guild.id, channel.id, self.data["name"], self.data.get("description"), self.data["prize"],
                 self.data.get("image"), self.data["goal_type"], self.data["goal"], self.data["winners"],
                 self.data.get("required_role"), self.data.get("bonus_role"), self.data.get("bonus_entries", 0),
                 interaction.user.id, now))
            giveaway_id = cur.lastrowid
            await db.commit()
        g = await db_fetchone("SELECT * FROM giveaways WHERE id=?", (giveaway_id,))
        message = await channel.send(embed=build_embed(g, 0, interaction.guild), view=GiveawayView(giveaway_id))
        await db_execute("UPDATE giveaways SET message_id=? WHERE id=?", (message.id, giveaway_id))
        bot.add_view(GiveawayView(giveaway_id), message_id=message.id)
        await interaction.response.send_message(f"Sorteio publicado em {channel.mention}!", ephemeral=True)
        self.stop()


@bot.tree.command(name="sorteios", description="Abre o painel para criar um sorteio")
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(title="🎉 Painel de Sorteios", description="1. Escolha o tipo da meta\n2. Selecione o canal\n3. Clique em Configurar\n4. Use Avançado se quiser cargos/imagem\n5. Publique", color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=AdminPanel(interaction.user.id), ephemeral=True)


@bot.tree.command(name="encerrar", description="Encerra um sorteio agora")
@app_commands.checks.has_permissions(administrator=True)
async def end(interaction: discord.Interaction, id_do_sorteio: int):
    g = await db_fetchone("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (id_do_sorteio, interaction.guild_id))
    if not g:
        return await interaction.response.send_message("Sorteio não encontrado.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    if await finish_giveaway(id_do_sorteio):
        await interaction.followup.send("Sorteio encerrado.", ephemeral=True)
    else:
        await interaction.followup.send("Esse sorteio já foi encerrado.", ephemeral=True)


@bot.tree.command(name="sortear_novamente", description="Escolhe novos vencedores")
@app_commands.checks.has_permissions(administrator=True)
async def reroll(interaction: discord.Interaction, id_do_sorteio: int):
    g = await db_fetchone("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (id_do_sorteio, interaction.guild_id))
    if not g:
        return await interaction.response.send_message("Sorteio não encontrado.", ephemeral=True)
    rows = await db_fetchall("SELECT user_id, entries FROM entries WHERE giveaway_id=?", (id_do_sorteio,))
    pool = [r["user_id"] for r in rows for _ in range(max(1, r["entries"]))]
    if not pool:
        return await interaction.response.send_message("Não há participantes.", ephemeral=True)
    winners = random.sample(list(set(pool)), min(g["winner_count"], len(set(pool))))
    await interaction.response.send_message("🔄 Novo(s) vencedor(es): " + ", ".join(f"<@{x}>" for x in winners))


@tasks.loop(seconds=60)
async def check_goals():
    for g in await db_fetchall("SELECT * FROM giveaways WHERE status='active'"):
        if await goal_reached(g):
            await finish_giveaway(g["id"])


@bot.event
async def on_ready():
    await setup_database()
    active = await db_fetchall("SELECT id, message_id FROM giveaways WHERE status='active' AND message_id IS NOT NULL")
    for g in active:
        bot.add_view(GiveawayView(g["id"]), message_id=g["message_id"])
    if not check_goals.is_running():
        check_goals.start()
    try:
        await bot.tree.sync()
    except discord.HTTPException:
        pass
    print(f"Bot conectado como {bot.user}")


if not TOKEN:
    raise RuntimeError("Defina DISCORD_TOKEN nas variáveis de ambiente.")
bot.run(TOKEN)

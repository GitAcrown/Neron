import io
import logging
import random
from datetime import datetime
from typing import Iterable

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

from common import dataio

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

class Birthdays(commands.Cog):
    """Gestion des anniversaires des utilisateurs"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.last_check = ''
        
    def __initialize_guilds(self, guilds: Iterable[discord.Guild]):
        default_settings = {
            'NotificationChannel': 0,
            'RoleID': 0,
            'SilentMentions': 1
        }
        self.data.build_settings_table(guilds, default_settings)
        
    def __initialize_users(self):
        query = """CREATE TABLE IF NOT EXISTS bdays (user_id INTEGER PRIMARY KEY, date TEXT)"""
        self.data.get('Users').execute(query)
    
    @commands.Cog.listener()
    async def on_ready(self):
        guilds = self.bot.guilds
        self.__initialize_guilds(guilds)
        self.__initialize_users()
        await self.bot.wait_until_ready()
        self.check_birthdays.start()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__initialize_guilds([guild])
        
    def cog_unload(self):
        self.data.close_all()
        self.check_birthdays.cancel()
        
    # Loop -----------------------------------------------
    
    @tasks.loop(seconds=30)
    async def check_birthdays(self):
        if self.last_check == datetime.now().strftime('%d/%m'):
            return
        
        logger.info("Vérification des anniversaires...")
        self.last_check = datetime.now().strftime('%d/%m')
        for guild in self.bot.guilds:
            channel_id : int = self.data.get_setting(guild, 'NotificationChannel', cast_as=int)
            role_id : int = self.data.get_setting(guild, 'RoleID', cast_as=int)
            silent = self.data.get_setting(guild, 'SilentMentions', cast_as=int)
            
            birthdays = self.get_birthdays_today(guild)
            
            if role_id:
                role = guild.get_role(role_id)
                if not role:
                    continue
                
                # On le retire aux membres qui ne doivent plus l'avoir
                for member in guild.members:
                    if member in birthdays:
                        continue
                    if role in member.roles:
                        await member.remove_roles(role, reason="Anniversaire terminé")
                        
                # On l'ajoute aux membres qui doivent l'avoir
                if not birthdays:
                    continue
                for member in birthdays:
                    if role not in member.roles:
                        await member.add_roles(role, reason="Anniversaire")
                        
            if channel_id:
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                rdm = random.choice(("Aujourd'hui c'est l'anniversaire de", "Nous fêtons aujourd'hui l'anniversaire de", "C'est l'ANNIVERSAIRE de", "Bon anniversaire à", "Joyeux anniversaire à"))
                if len(birthdays) == 1:
                    msg = f"## {rdm} {birthdays[0].mention} !"
                else:
                    msg = f"## {rdm} {', '.join([m.mention for m in birthdays[:-1]])} et {birthdays[-1].mention} !"
                msg += f" 🎉"
                
                astro = self.get_zodiac_sign(datetime.now())
                astro = f" · {astro[1]}" if astro else ''
                msg += f"\n**{datetime.now().strftime('%d/%m')}**{astro}"
                await channel.send(msg, silent=bool(silent))
        
    # Users -----------------------------------------------
    
    def get_user_birthday(self, user: discord.User | discord.Member) -> datetime | None:
        r = self.data.get('Users').fetchone("SELECT date FROM bdays WHERE user_id = ?", (user.id,))
        if r:
            return datetime.strptime(r['date'], '%d/%m')
    
    def set_user_birthday(self, user: discord.User | discord.Member, date: str):
        self.data.get('Users').execute("INSERT OR REPLACE INTO bdays VALUES (?, ?)", (user.id, date))
        
    def remove_user_birthday(self, user: discord.User | discord.Member):
        self.data.get('Users').execute("DELETE FROM bdays WHERE user_id = ?", (user.id,))
        
    def get_user_embed(self, user: discord.User | discord.Member) -> discord.Embed:
        date = self.get_user_birthday(user)
        if not date:
            return discord.Embed(title=f"Anniversaire de **{user.display_name}**", description="Aucune date d'anniversaire définie", color=0x2b2d31)
        
        dt = date.replace(year=datetime.now().year)
        msg = f"**Date ·** {dt.strftime('%d/%m')}\n"

        # On calcule la date du prochain anniversaire
        today = datetime.now()
        if today >= dt:
            next_date = dt.replace(year=today.year + 1)
        else:
            next_date = dt
        msg += f"**Prochain ·** <t:{int(next_date.timestamp())}:D>\n"
    
        astro = self.get_zodiac_sign(dt)
        if astro:
            msg += f"**Signe astro. ·** {' '.join(astro)}"
        
        embed = discord.Embed(title=f"Anniversaire de **{user.display_name}**", description=msg, color=0x2b2d31)
        embed.set_thumbnail(url=user.display_avatar.url)
        return embed
        
    def get_zodiac_sign(self, date: datetime) -> tuple[str, str] | None:
        zodiacs = [(120, 'Capricorne', '♑'), (218, 'Verseau', '♒'), (320, 'Poisson', '♓'), (420, 'Bélier', '♈'), (521, 'Taureau', '♉'),
           (621, 'Gémeaux', '♊'), (722, 'Cancer', '♋'), (823, 'Lion', '♌'), (923, 'Vierge', '♍'), (1023, 'Balance', '♎'),
           (1122, 'Scorpion', '♏'), (1222, 'Sagittaire', '♐'), (1231, 'Capricorne', '♑')]
        date_number = int(''.join((str(date.month), '%02d' % date.day)))
        for z in zodiacs:
            if date_number <= z[0]:
                return z[1], z[2]
    
    def get_birthdays_from(self, guild: discord.Guild) -> dict[discord.Member, datetime]:
        r = self.data.get('Users').fetchall("SELECT * FROM bdays")
        members = {m.id: m for m in guild.members}
        bdays = {}
        for u in r:
            if u['user_id'] in members:
                bdays[members[u['user_id']]] = datetime.strptime(u['date'], '%d/%m')
        return bdays
    
    def get_birthdays_today(self, guild: discord.Guild) -> list[discord.Member]:
        return [m for m, d in self.get_birthdays_from(guild).items() if d.month == datetime.now().month and d.day == datetime.now().day]
    
    def check_date_format(self, date: str) -> bool:
        try:
            datetime.strptime(date, '%d/%m')
            return True
        except ValueError:
            return False
        
    # COMMANDS =====================================================
    
    bday_group = app_commands.Group(name='bday', description="Gestion des anniversaires")
    
    @bday_group.command(name='set')
    async def _set_birthday(self, interaction: Interaction, date: str):
        """Définir votre date d'anniversaire
        
        :param date: Date au format JJ/MM"""
        if not self.check_date_format(date):
            return await interaction.response.send_message("**Format invalide** · La date doit être au format JJ/MM", ephemeral=True)
        
        self.set_user_birthday(interaction.user, date)
        await interaction.response.send_message(f"**Date d'anniversaire définie** · Votre date d'anniversaire est le `{date}`", ephemeral=True)
        
    @bday_group.command(name='remove')
    async def _remove_birthday(self, interaction: Interaction):
        """Supprimer votre date d'anniversaire"""
        if not self.get_user_birthday(interaction.user):
            return await interaction.response.send_message("**Date d'anniversaire inexistante** · Définissez votre date d'anniversaire avec `/bday set`", ephemeral=True)
        
        self.remove_user_birthday(interaction.user)
        await interaction.response.send_message("**Date d'anniversaire supprimée** · Vous n'avez plus de date d'anniversaire définie", ephemeral=True)
        
    @bday_group.command(name='get')
    @app_commands.rename(user='membre')
    async def _get_birthday(self, interaction: Interaction, user: discord.Member | None= None):
        """Afficher la date d'anniversaire d'un membre
        
        :param user: Autre membre dont on veut afficher la date d'anniversaire"""
        if not user:
            user = interaction.user # type: ignore
        await interaction.response.send_message(embed=self.get_user_embed(user), ephemeral=True) # type: ignore
        
    @bday_group.command(name='next')
    @app_commands.rename(limit='limite')
    async def _next_birthdays(self, interaction: Interaction, limit: app_commands.Range[int, 1, 50] = 10):
        """Afficher les prochains anniversaires
        
        :param limit: Nombre d'anniversaires à afficher"""
        if not isinstance(interaction.guild, discord.Guild):
            return await interaction.response.send_message("**Commande non disponible** · Cette commande n'est disponible que sur un serveur", ephemeral=True)
        
        birthdays = self.get_birthdays_from(interaction.guild)
        if not birthdays:
            return await interaction.response.send_message("**Aucun anniversaire** · Aucun membre n'a défini de date d'anniversaire")
        
        today = datetime.now()
        # On trie les anniversaires par date en ajoutant un an si la date est passée
        for m, d in birthdays.items():
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
            birthdays[m] = d
        listebday = sorted(birthdays.items(), key=lambda x: x[1].timestamp())
        
        if not listebday:
            return await interaction.response.send_message("**Aucun anniversaire** · Aucun anniversaire n'est prévu dans les prochains jours")
        
        msg = ''
        year_changed = False
        for b in listebday[:limit]:
            user, date = b
            if date.year != today.year and not year_changed:
                msg += f"**---- {date.year} ----**\n"
                year_changed = True
            msg += f"{user.mention} · <t:{int(date.timestamp())}:D>\n"
        embed = discord.Embed(title="Prochains anniversaires", description=msg, color=0x2b2d31)
        embed.set_footer(text=f"{limit}/{len(listebday)} anniversaires affichés")
        await interaction.response.send_message(embed=embed)
        
    mod_group = app_commands.Group(name='configbdays', description="Paramètres des anniversaires", default_permissions=discord.Permissions(administrator=True), guild_only=True)

    @mod_group.command(name='setuser')
    @app_commands.rename(user='membre')
    async def _set_user_birthday(self, interaction: Interaction, user: discord.Member, date: str | None):
        """Définir la date d'anniversaire d'un membre

        :param user: Membre dont on veut définir la date d'anniversaire
        :param date: Date au format JJ/MM, ou rien pour supprimer la date d'anniversaire"""
        if not date:
            self.remove_user_birthday(user)
            return await interaction.response.send_message(f"**Date d'anniversaire supprimée** · {user.mention} n'a plus de date d'anniversaire définie")
        
        if not self.check_date_format(date):
            return await interaction.response.send_message("**Format invalide** · La date doit être au format JJ/MM")
        
        self.set_user_birthday(user, date)
        await interaction.response.send_message(f"**Date d'anniversaire définie** · La date d'anniversaire de {user.mention} est le `{date}`", ephemeral=True)
        
    @mod_group.command(name='notify')
    @app_commands.rename(channel='salon')
    async def _notify_birthday(self, interaction: Interaction, channel: discord.TextChannel | None = None):
        """Définir un salon où sera envoyé un message à chaque anniversaire
        
        :param channel: Salon où envoyer les messages, ou rien pour supprimer le salon actuel"""
        if not channel:
            self.data.update_settings(interaction.guild, {'NotificationChannel': 0})
            return await interaction.response.send_message("**Salon supprimé** · Les messages d'anniversaire ne seront plus envoyés", ephemeral=True)
        
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("**Salon invalide** · Le salon sélectionné doit être un salon textuel classique")
        
        self.data.update_settings(interaction.guild, {'NotificationChannel': channel.id})
        await interaction.response.send_message(f"**Salon défini** · Les messages d'anniversaire seront envoyés dans {channel.mention}", ephemeral=True)
        
    @mod_group.command(name='role')
    async def _role_birthday(self, interaction: Interaction, role: discord.Role | None = None):
        """Définir un rôle à attribuer aux membres dont c'est l'anniversaire
        
        :param role: Rôle à attribuer, ou rien pour supprimer le rôle actuel"""
        if not role:
            self.data.update_settings(interaction.guild, {'RoleID': 0})
            return await interaction.response.send_message("**Rôle supprimé** · Les membres n'auront plus de rôle attribué à leur anniversaire", ephemeral=True)
        
        if not isinstance(role, discord.Role):
            return await interaction.response.send_message("**Rôle invalide** · Le rôle sélectionné doit être un rôle")
        
        self.data.update_settings(interaction.guild, {'RoleID': role.id})
        await interaction.response.send_message(f"**Rôle défini** · Le rôle {role.mention} sera attribué automatiquement aux membres dont c'est l'anniversaire", ephemeral=True)
        
    @mod_group.command(name='silent')
    async def _silent_birthday(self, interaction: Interaction, silent: bool):
        """Définir si les messages d'anniversaire doivent notifier (ping) les membres ou non
        
        :param silent: `True` pour ne pas mentionner les membres, `False` pour les mentionner"""
        self.data.update_settings(interaction.guild, {'SilentMentions': int(silent)})
        await interaction.response.send_message(f"**Mentions définies** · Les messages d'anniversaire {'ne mentionneront pas' if silent else 'mentionneront'} les membres", ephemeral=True)
        
    # EXPORT =====================================================
    
    @commands.command(name='exportbdays')
    @commands.is_owner()
    async def _export_birthdays(self, ctx: commands.Context):
        """Exporter les dates d'anniversaire de tous les membres du serveur sous le format user_id:jj/mm"""
        r = self.data.get('Users').fetchall("SELECT * FROM bdays")
        if not r:
            return await ctx.send("**Aucun anniversaire** · Aucun membre n'a défini de date d'anniversaire")
        
        msg = ''
        for u in r:
            msg += f"{u['user_id']}:{u['date']}\n"
        await ctx.send(f"**Dates d'anniversaire** · {len(r)} dates d'anniversaire trouvées", file=discord.File(filename='birthdays.txt', fp=io.BytesIO(msg.encode('utf-8'))))
        
async def setup(bot):
    await bot.add_cog(Birthdays(bot))

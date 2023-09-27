import logging
from datetime import datetime
from typing import Any, Iterable

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

from common import dataio

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

MESSAGE_EXPIRATION_DELAY = 24 * 60 * 60 # 24h 

class Starboard(commands.Cog):
    """Compilateur des messages préférés des membres du serveur"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    def __initialize_guilds(self, guilds: Iterable[discord.Guild]):
        default_settings = {
            'Enabled': 0,
            'Channel': None,
            'Threshold': 5,
            'Emote': '⭐',
            'NotifyNearThreshold': 0
        }
        self.data.build_settings_table(guilds, default_settings)
        
        messages_query = """CREATE TABLE IF NOT EXISTS messages (
            message_id INTEGER PRIMARY KEY,
            starboard_message_id INTEGER,
            created_at INTEGER
            )"""
        votes_query = """CREATE TABLE IF NOT EXISTS votes (
            unique_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            user_id INTEGER,
            FOREIGN KEY(message_id) REFERENCES messages(message_id)
            )"""
        self.data.bulk_initialize(guilds, (messages_query, votes_query))
    
    @commands.Cog.listener()
    async def on_ready(self):
        self.__initialize_guilds(self.bot.guilds)
        await self.bot.wait_until_ready()
        self.task_message_expire.start()
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__initialize_guilds([guild])
        
    def cog_unload(self):
        self.data.close_all()
        self.task_message_expire.cancel()
        
    # Tâches ----------------------------------
    
    @tasks.loop(hours=12)
    async def task_message_expire(self):
        logger.info("Effacement des messages expirés...")
        for guild in self.bot.guilds:
            self.clean_expired_messages(guild)
        
    # Message tracking ----------------------------------
    
    def get_message_data(self, guild: discord.Guild, message_id: int) -> dict[str, Any] | None:
        r = self.data.get(guild).fetchone("SELECT * FROM messages WHERE message_id = ?", (message_id,))
        return r if r else None
    
    def set_message_data(self, guild: discord.Guild, message_id: int, starboard_message_id: int = 0):
        self.data.get(guild).execute("INSERT OR REPLACE INTO messages VALUES (?, ?, ?)", (message_id, starboard_message_id, int(datetime.now().timestamp())))
        
    def remove_message_data(self, guild: discord.Guild, message_id: int):
        self.data.get(guild).execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
        
    def clean_expired_messages(self, guild: discord.Guild):
        self.data.get(guild).execute("DELETE FROM messages WHERE created_at < ?", (int(datetime.now().timestamp()) - MESSAGE_EXPIRATION_DELAY,))
        # On supprime aussi les votes associés
        self.data.get(guild).execute("DELETE FROM votes WHERE message_id NOT IN (SELECT message_id FROM messages)")
        
    # Vote tracking ----------------------------------
    
    def get_message_votes(self, guild: discord.Guild, message_id: int):
        r = self.data.get(guild).fetchall("SELECT user_id FROM votes WHERE message_id = ?", (message_id,))
        return [x['user_id'] for x in r] if r else []
    
    def add_message_vote(self, guild: discord.Guild, message_id: int, user_id: int) -> bool:
        """Ajoute un vote au message donné. Renvoie True si le vote a été ajouté, False si le vote existait déjà."""
        if not self.get_message_data(guild, message_id):
            self.set_message_data(guild, message_id) # On initialise le message
        if user_id in self.get_message_votes(guild, message_id):
            return False
        self.data.get(guild).execute("INSERT INTO votes (message_id, user_id) VALUES (?, ?)", (message_id, user_id))
        return True
        
    def remove_message_vote(self, guild: discord.Guild, message_id: int, user_id: int):
        if not self.get_message_data(guild, message_id):
            return
        self.data.get(guild).execute("DELETE FROM votes WHERE message_id = ? AND user_id = ?", (message_id, user_id))
        
    # Starboard ----------------------------------
    
    def get_starboard_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = self.data.get_setting(guild, 'Channel', cast_as=int)
        return guild.get_channel(channel_id) if channel_id else None # type: ignore # Ne peut être autre chose qu'un TextChannel puisque c'est vérifié lors de la configuration
    
    async def get_starboard_message(self, guild: discord.Guild, message_id: int) -> discord.Message | None:
        """Renvoie le message du starboard correspondant au message donné, ou None si aucun n'existe"""
        starboard_channel = self.get_starboard_channel(guild)
        if not starboard_channel:
            return None
        r = self.data.get(guild).fetchone("SELECT starboard_message_id FROM messages WHERE message_id = ?", (message_id,))
        if not r:
            return None
        try:
            return await starboard_channel.fetch_message(int(r['starboard_message_id']))
        except discord.NotFound:
            return None

    async def starboard_embed(self, message: discord.Message, *, starboard_message: discord.Message | None = None) -> discord.Embed:
        """Génère l'embed du message à afficher dans le starboard"""
        guild = message.guild
        
        data = self.get_message_data(guild, message.id) # type: ignore
        if not data:
            raise ValueError(f"Message {message.id} not found in starboard")
        votes = self.get_message_votes(guild, message.id) # type: ignore
        threshold = self.data.get_setting(guild, 'Threshold', cast_as=int)
        emote = self.data.get_setting(guild, 'Emote')
        
        if starboard_message:
            # On économise du temps de calcul en ne mettant à jour que le footer
            embed = starboard_message.embeds[0]
            embed.set_footer(text=f"{emote} {len(votes)}/{threshold}")
            return embed
        
        reply_text = ''
        reply_thumb = None
        if message.reference and message.reference.message_id:
            try:
                reference_msg : discord.Message = await message.channel.fetch_message(message.reference.message_id)
                reply_text = f"> **{reference_msg.author.name}** · <t:{int(reference_msg.created_at.timestamp())}>\n> {reference_msg.content if reference_msg.content else '[Média ci-contre]'}\n\n"
                _reply_img = [a for a in reference_msg.attachments if a.content_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']]
                if _reply_img:
                    reply_thumb = _reply_img[0]
            except Exception as e:
                logger.info(e, exc_info=True)
        
        message_content = message.content
        # message_content += f"\n[→ Aller au message]({message.jump_url})"
        
        content = reply_text + message_content
        votes = len(votes)
        footxt = f"{emote} {votes}/{threshold}"
        
        em = discord.Embed(description=content, timestamp=message.created_at, color=0x2b2d31)
        em.set_author(name=message.author.name, icon_url=message.author.display_avatar.url)
        em.set_footer(text=footxt)
        
        image_preview = None
        media_links = []
        for a in message.attachments:
            if a.content_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp'] and not image_preview:
                image_preview = a.url
            else:
                media_links.append(a.url)
        for msge in message.embeds:
            if msge.image and not image_preview:
                image_preview = msge.image.url
            elif msge.thumbnail and not image_preview:
                image_preview = msge.thumbnail.url
        
        if image_preview:
            em.set_image(url=image_preview)
        if reply_thumb:
            em.set_thumbnail(url=reply_thumb)
        if media_links:
            linkstxt = [f"[[{l.split('/')[-1]}]]({l})" for l in media_links]
            em.add_field(name="Média(s)", value='\n'.join(linkstxt))
            
        return em
    
    async def handle_starboard_message(self, message: discord.Message, *, ignore_threshold: bool = False):
        """Met à jour le message du starboard correspondant au message donné ou le crée s'il n'existe pas"""
        guild = message.guild
        starboard_channel = self.get_starboard_channel(guild) # type: ignore
        if not starboard_channel:
            return
        
        data = self.get_message_data(guild, message.id) # type: ignore
        if not data:
            return
        
        starboard_message = await self.get_starboard_message(guild, message.id) # type: ignore
        if starboard_message:
            return await starboard_message.edit(embed=await self.starboard_embed(message, starboard_message=starboard_message))
            
        threshold = self.data.get_setting(guild, 'Threshold', cast_as=int)
        votes = self.get_message_votes(guild, message.id) # type: ignore
        emote = self.data.get_setting(guild, 'Emote')
        notify = bool(self.data.get_setting(guild, 'NotifyNearThreshold', cast_as=int))
        if len(votes) < threshold and not ignore_threshold:
            # Si le message à la moitié + 1 vote et qu'on doit notifier, on notifie
            if notify and len(votes) == threshold // 2 + 1 and threshold > 1:
                await message.reply(f"`{emote}` Ce message a atteint plus de la moitié du seuil de votes requis pour apparaître dans {starboard_channel.mention} (**{len(votes)}**/{threshold}) !", mention_author=False, delete_after=60)
            return
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Message d'origine", url=message.jump_url))
        
        em = await self.starboard_embed(message)
        try:
            starboard_message = await starboard_channel.send(embed=em, view=view)
        except discord.Forbidden:
            return await message.channel.send(f"Je n'ai pas la permission d'envoyer des messages dans le salon {starboard_channel.mention} pour y intégrer des messages.", mention_author=False)
        self.set_message_data(guild, message.id, starboard_message.id) # type: ignore
        await message.reply(f"## `{emote}` Ce message a rejoint le salon {starboard_channel.mention} !", mention_author=False, delete_after=60)
        
    # Détection des votes ----------------------------------
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel | discord.Thread)):
            return
        if not channel.guild:
            return
        if not channel.permissions_for(channel.guild.me).read_message_history:
            return
        
        guild = channel.guild
        if not bool(self.data.get_setting(guild, 'Enabled', cast_as=int)):
            return
        
        emote = payload.emoji.name
        if emote != self.data.get_setting(guild, 'Emote'):
            return
        
        starboard_channel = self.get_starboard_channel(guild)
        if not starboard_channel:
            return
        
        message = await channel.fetch_message(payload.message_id)
        if not message:
            return
        if message.created_at.timestamp() < int(datetime.now().timestamp()) - MESSAGE_EXPIRATION_DELAY: # On ne prend pas en compte les votes sur les messages expirés
            return
        
        member = guild.get_member(payload.user_id)
        if not member:
            return
        
        if self.add_message_vote(guild, message.id, member.id): # type: ignore
            await self.handle_starboard_message(message)
        
    # COMMANDS ----------------------------------
    
    config_sb_group = app_commands.Group(name='starboard', description="Configuration du salon de compilation des messages favoris", guild_only=True, default_permissions=discord.Permissions(manage_channels=True))
        
    @config_sb_group.command(name='enable')
    @app_commands.rename(enabled='activer')
    async def config_sb_enable(self, interaction: Interaction, enabled: bool):
        """Active ou désactive le salon de compilation des messages favoris
        
        :param enabled: True pour activer, False pour désactiver"""
        self.data.update_settings(interaction.guild, {'Enabled': int(enabled)})
        await interaction.response.send_message(f"**Paramètre modifié** · Le salon de compilation des messages favoris est maintenant **{'activé' if enabled else 'désactivé'}**.", ephemeral=True)
        
    @config_sb_group.command(name='manual')
    async def config_manual_add(self, interaction: Interaction, message_id: int):
        """Ajoute manuellement un message au salon de compilation des messages favoris

        :param message_id: L'ID du message à ajouter"""
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("**Erreur** · Le salon doit être un salon textuel classique.", ephemeral=True)
        message = await interaction.channel.fetch_message(message_id)
        if not message:
            return await interaction.response.send_message("**Erreur** · Le message n'a pas été trouvé.", ephemeral=True)
        await self.handle_starboard_message(message)
        await interaction.response.send_message(f"**Message ajouté** · Le message a été ajouté au salon de compilation des messages favoris.", ephemeral=True, delete_after=10)
        
    @config_sb_group.command(name='channel')
    @app_commands.rename(channel='salon')
    async def config_sb_channel(self, interaction: Interaction, channel: discord.TextChannel):
        """Définit le salon de compilation des messages favoris
        
        :param channel: Le salon de compilation des messages favoris"""
        # On vérifie que le salon est bien un salon textuel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("**Erreur** · Le salon doit être un salon textuel classique.", ephemeral=True)
        
        # On vérifie que le bot a les permissions nécessaires pour poster et éditer des messages dans le salon
        if not interaction.guild:
            return await interaction.response.send_message("**Erreur** · Le salon doit être sur le serveur.", ephemeral=True)
        
        if not channel.permissions_for(interaction.guild.me).send_messages and not channel.permissions_for(interaction.guild.me).read_message_history:
            return await interaction.response.send_message("**Permissions manquantes** · Assurez-vous de me donner un salon dans lequel je puisse poster.", ephemeral=True)
        
        self.data.update_settings(interaction.guild, {'Channel': channel.id})
        await interaction.response.send_message(f"**Paramètre modifié** · Le salon de compilation des messages favoris est maintenant {channel.mention}.", ephemeral=True)
        
    @config_sb_group.command(name='threshold')
    @app_commands.rename(threshold='seuil')
    async def config_sb_threshold(self, interaction: Interaction, threshold: app_commands.Range[int, 0]):
        """Définit le nombre de votes nécessaires pour qu'un message apparaisse dans le salon de compilation des messages favoris
        
        :param threshold: Le nombre de votes nécessaires"""
        self.data.update_settings(interaction.guild, {'Threshold': threshold})
        await interaction.response.send_message(f"**Paramètre modifié** · Le seuil de votes des messages favoris est maintenant de **{threshold}**.", ephemeral=True)
        
    @config_sb_group.command(name='emote')
    @app_commands.rename(emote='emoji')
    async def config_sb_emote(self, interaction: Interaction, emote: str):
        """Définit l'emote unicode utilisé pour voter
        
        :param emote: L'emoji unicode utilisé pour voter"""
        # On vérifie que l'emote est bien un emoji de base Discord
        if type(emote) is not str or len(emote) > 1:
            return await interaction.response.send_message("**Erreur** · L'emoji doit être un emoji unicode de base Discord.", ephemeral=True)
        self.data.update_settings(interaction.guild, {'Emote': emote})
        
        await interaction.response.send_message(f"**Paramètre modifié** · L'emoji utilisé pour voter est maintenant {emote}.", ephemeral=True)
        
    @config_sb_group.command(name='notify')
    @app_commands.rename(notify='notifier')
    async def config_sb_notify(self, interaction: Interaction, notify: bool):
        """Active ou désactive la notification à l'approche du seuil de votes par un message
        
        :param notify: True pour activer, False pour désactiver"""
        self.data.update_settings(interaction.guild, {'NotifyNearThreshold': int(notify)})
        await interaction.response.send_message(f"**Paramètre modifié** · Les notifications à l'approche du seuil sont maintenant **{'activées' if notify else 'désactivées'}**.\n*Notez que cette notification ne se fera pas si votre seuil de vote est inférieur à 3.*", ephemeral=True)
            
async def setup(bot):
    await bot.add_cog(Starboard(bot))

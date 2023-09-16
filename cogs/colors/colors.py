import logging
import re
from io import BytesIO
from typing import Iterable

import colorgram
import discord
import requests
from discord import Interaction, app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import dataio

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

DISCORD_INVALID_COLOR = '000000' # Couleur utilisée par Discord pour les rôles sans couleur

class Colors(commands.Cog):
    """Système de rôles de couleurs"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    def __initialize_guilds(self, guilds: Iterable[discord.Guild]):
        default_settings = {
            'Enabled': 1,
            'BoundaryRole': 0,
            'LimitToRole': 0
        }
        self.data.build_settings_table(guilds, default_settings)
        
    def __initialize_users(self):
        query = """CREATE TABLE IF NOT EXISTS config (
            user_id INTEGER PRIMARY KEY,
            autoswitch INTEGER DEFAULT 0 CHECK(autoswitch IN (0, 1)))""" # Si l'utilisateur a activé le changement automatique de couleur
        self.data.get('Users').execute(query)
        
    def cog_unload(self):
        self.data.close_all()
        
    @commands.Cog.listener()
    async def on_ready(self):
        self.__initialize_guilds(self.bot.guilds)
        self.__initialize_users()
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__initialize_guilds([guild])
        
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.is_enabled(before.guild):
            return

        if self.is_role_limited(after):
            return
            
        if before.display_avatar != after.display_avatar:
            if self.is_autoswitch_enabled(after):
                colors = await self.get_avatar_colors(after, count=1)
                if colors:
                    role = await self.fetch_role(after.guild, colors[0], requested_by=after)
                    # On retire le rôle actuel de couleur
                    current_role = self.get_user_color_role(after)
                    if current_role:
                        try:
                            await after.remove_roles(current_role, reason=f'Rôle de couleur automatique pour {after}')
                        except discord.Forbidden:
                            logger.warning(f'Impossible de retirer le rôle de couleur automatique à {after}')
                        except discord.HTTPException:
                            logger.warning(f'Impossible de retirer le rôle de couleur automatique à {after}')
                    # On ajoute le nouveau rôle de couleur  
                    try:
                        await after.add_roles(role, reason=f'Rôle de couleur automatique pour {after}')
                    except discord.Forbidden:
                        logger.warning(f'Impossible d\'ajouter le rôle de couleur automatique à {after}')
                    except discord.HTTPException:
                        logger.warning(f'Impossible d\'ajouter le rôle de couleur automatique à {after}')
                    # On envoie un message
                    em = discord.Embed(title='M.à.j automatique du rôle de couleur', description=f'Vous avez obtenu le rôle de couleur **#{role.name}** sur ***{after.guild.name}***', color=role.color)
                    em.set_thumbnail(url=self.get_color_block(colors[0]))
                    em.set_footer(text=self.get_color_name(colors[0]) or 'Nom inconnu')
                    try:
                        await after.send(embed=em)
                    except discord.Forbidden:
                        logger.warning(f'Impossible d\'envoyer un message à {after}')
                else:
                    logger.warning(f'Impossible de récupérer la couleur de l\'avatar de {after}')
        
    # Guild settings ------------------------------------------------------
    
    def is_enabled(self, guild: discord.Guild) -> bool:
        """Renvoie si le système est activé"""
        return bool(self.data.get_setting(guild, 'Enabled', cast_as=int))
    
    def get_boundary_role(self, guild: discord.Guild) -> discord.Role | None:
        """Renvoie le rôle servant de repère pour le rangement des rôles de couleurs"""
        boundary_role = self.data.get_setting(guild, 'BoundaryRole', cast_as=int)
        return guild.get_role(boundary_role) if boundary_role else None
        
    def set_boundary_role(self, guild: discord.Guild, role: discord.Role | None):
        """Définit le rôle servant de repère pour le rangement des rôles de couleurs"""
        self.data.update_settings(guild, {'BoundaryRole': role.id if role else 0})
        
    def get_limit_to_role(self, guild: discord.Guild) -> discord.Role | None:
        """Renvoie le rôle limitant l'utilisation des rôles de couleurs aux membres le possédant"""
        limit_to_role = self.data.get_setting(guild, 'LimitToRole', cast_as=int)
        return guild.get_role(limit_to_role) if limit_to_role else None
    
    def set_limit_to_role(self, guild: discord.Guild, role: discord.Role | None):
        """Définit le rôle limitant l'utilisation des rôles de couleurs aux membres le possédant"""
        self.data.update_settings(guild, {'LimitToRole': role.id if role else 0})
        
    def is_role_limited(self, member: discord.Member) -> bool:
        """Vérifie si l'utilisateur a le droit de changer de couleur"""
        limit_to_role = self.get_limit_to_role(member.guild)
        if limit_to_role and limit_to_role not in member.roles:
            return True
        return False
        
    # User settings -------------------------------------------------------
    
    def add_user_to_config(self, user: discord.User | discord.Member):
        """Ajoute l'utilisateur à la base de données s'il n'y est pas déjà"""
        self.data.get('Users').execute("INSERT OR IGNORE INTO config (user_id) VALUES (?)", (user.id,))
    
    def is_autoswitch_enabled(self, user: discord.User | discord.Member) -> bool:
        """Renvoie si l'utilisateur a activé le changement automatique de couleur"""
        self.add_user_to_config(user)
        r = self.data.get('Users').fetchone("SELECT autoswitch FROM config WHERE user_id = ?", (user.id,))
        return bool(r['autoswitch']) if r else False
    
    def set_autoswitch(self, user: discord.User | discord.Member, enabled: bool):
        """Définit si l'utilisateur a activé le changement automatique de couleur"""
        self.add_user_to_config(user)
        self.data.get('Users').execute("UPDATE config SET autoswitch = ? WHERE user_id = ?", (int(enabled), user.id))
        
    # Role management -----------------------------------------------------
    
    def get_color_roles(self, guild: discord.Guild) -> list[discord.Role]:
        """Renvoie la liste des rôles de couleurs"""
        return [r for r in guild.roles if r.name.startswith('#') and r.name[1:].isalnum()]
    
    def get_color_role(self, guild: discord.Guild, hex_color: str) -> discord.Role | None:
        """Renvoie le rôle de couleur correspondant à la couleur donnée"""
        return discord.utils.get(self.get_color_roles(guild), name=f'#{hex_color}')
    
    def get_user_color_role(self, member: discord.Member) -> discord.Role | None:
        """Renvoie le rôle de couleur de l'utilisateur"""
        for role in member.roles:
            if role.name.startswith('#') and role.name[1:].isalnum():
                return role
        return None
    
    def is_recyclable(self, role: discord.Role, ignore_members: Iterable[discord.Member]) -> bool:
        """Renvoie si le rôle est recyclable (aucun membre ne le possède)"""
        return not any([m for m in role.members if m not in ignore_members])
    
    def is_color_displayed(self, member: discord.Member) -> bool:
        """Renvoie si le rôle de couleur de l'utilisateur est affiché"""
        role = self.get_user_color_role(member)
        if role and role.color == member.color:
            return True
        return False
    
    async def fetch_role(self, guild: discord.Guild, hex_color: str, *, requested_by: discord.Member | None = None) -> discord.Role:
        """Récupère le rôle de couleur correspondant à la couleur donnée, le crée ou le recycle depuis un rôle sans membre s'il n'existe pas"""
        # On vérifie s'il existe déjà
        role = self.get_color_role(guild, hex_color)
        if role:
            return role
         
        # On vérifie si l'autre rôle de couleur possédé par le membre est recyclable (s'il est le seul à le posséder)
        if requested_by:
            role = self.get_user_color_role(requested_by)
            if role and self.is_recyclable(role, [requested_by]):
                # On le modifie et on le renvoie
                await role.edit(name=f'#{hex_color.upper()}', color=discord.Color(int(hex_color, 16)), reason=f'Rôle recyclé pour {requested_by}')
        
        # On vérifie si un autre rôle recyclable existe
        for role in self.get_color_roles(guild):
            if self.is_recyclable(role, []):
                # On le modifie et on le renvoie
                await role.edit(name=f'#{hex_color.upper()}', color=discord.Color(int(hex_color, 16)), reason=f'Rôle recyclé pour {requested_by}')
                return role
            
        # Sinon on crée un nouveau rôle et on le range
        role = await guild.create_role(name=f'#{hex_color.upper()}', color=discord.Color(int(hex_color, 16)), reason=f'Rôle créé pour {requested_by}')
        await self.move_role(role)
        return role
    
    async def delete_role(self, role: discord.Role):
        """Supprime le rôle de couleur"""
        await role.delete(reason='Rôle de couleur supprimé')
        
    async def move_role(self, role: discord.Role, *, position: int = 0):
        """Range le rôle de couleur à la position donnée ou en dessous du rôle servant de repère si aucune position n'est donnée"""
        boundary_role = self.get_boundary_role(role.guild)
        if boundary_role:
            await role.edit(position=boundary_role.position - 1)
        else:
            await role.edit(position=position)
            
    async def bulk_move_roles(self, guild: discord.Guild):
        """Range tous les rôles de couleurs après le rôle servant de repère"""
        roles = self.get_color_roles(guild)
        if not roles:
            return
        # On les range par couleur
        roles = sorted(roles, key=lambda r: int(r.name[1:], 16))
        boundary_role = self.get_boundary_role(guild)
        if boundary_role:
            await guild.edit_role_positions({r: boundary_role.position - 1 for r in roles}, reason='Rangement auto. des rôles de couleurs')
        
    async def clean_roles(self, guild: discord.Guild):
        """Supprime tous les rôles de couleurs inutilisés"""
        roles = self.get_color_roles(guild)
        if not roles:
            return
        for role in roles:
            if self.is_recyclable(role, []):
                await self.delete_role(role)
        
    # Color management ----------------------------------------------------
    
    async def get_avatar_colors(self, member: discord.Member, *, count: int = 5) -> list[str]:
        """Renvoie les couleurs dominantes de l'avatar de l'utilisateur"""
        avatar = await member.display_avatar.read()
        avatar = Image.open(BytesIO(avatar))
        colors = colorgram.extract(avatar.resize((100, 100)), count)
        colors = [f'{c.rgb.r:02x}{c.rgb.g:02x}{c.rgb.b:02x}' for c in colors]
        return [c for c in colors if c != DISCORD_INVALID_COLOR]
    
    def get_color_block(self, hex_color: str) -> str:
        """Renvoie un bloc de couleur en 100x100 représentant la couleur donnée"""
        hex_color = hex_color.lstrip('#')
        return f'https://dummyimage.com/100/{hex_color}/{hex_color}.png'
    
    def get_color_name(self, hex_color: str) -> str | None:
        """Renvoie le nom de la couleur donnée"""
        hex_color = hex_color.lstrip('#')
        r = requests.get(f'https://www.thecolorapi.com/id?hex={hex_color}')
        if r.status_code != 200:
            return None
        return r.json()['name']['value']
    
    def draw_image_palette(self, img: str | BytesIO, n_colors: int = 5) -> Image.Image:
        """Ajoute la palette de N couleurs extraite de l'image sur le côté de celle-ci avec leurs codes hexadécimaux"""
        path = str(self.data.bundled_data_path)
        image = Image.open(img).convert("RGBA")
        colors : list[colorgram.Color] = colorgram.extract(image.resize((100, 100)), n_colors)
        image = ImageOps.contain(image, (500, 500))
        iw, ih = image.size
        w, h = (iw + 100, ih)
        font = ImageFont.truetype(f'{path}/RobotoRegular.ttf', 18)   
        palette = Image.new('RGBA', (w, h), color='white')
        maxcolors = h // 30
        if len(colors) > maxcolors:
            colors = colors[:maxcolors]
        blockheight = h // len(colors)
        for i, color in enumerate(colors):
            if i == len(colors) - 1:
                palette.paste(color.rgb, (iw, i * blockheight, iw + 100, h))
            else:
                palette.paste(color.rgb, (iw, i * blockheight, iw + 100, i * blockheight + blockheight))
            draw = ImageDraw.Draw(palette)
            hex_color = f'#{color.rgb[0]:02x}{color.rgb[1]:02x}{color.rgb[2]:02x}'.upper()
            if color.rgb[0] + color.rgb[1] + color.rgb[2] < 382:
                draw.text((iw + 10, i * blockheight + 10), f'{hex_color}', fill='white', font=font)
            else:
                draw.text((iw + 10, i * blockheight + 10), f'{hex_color}', fill='black', font=font)
        palette.paste(image, (0, 0))
        return palette
    
    # COMMANDES ===========================================================
    
    @app_commands.command(name='palette')
    @app_commands.rename(colors='nb_couleurs', file='fichier', user='utilisateur')
    async def _get_img_palette(self, interaction: Interaction, colors: app_commands.Range[int, 3, 10] = 5, url: str | None = None, file: discord.Attachment | None = None, user: discord.Member | None = None):
        """Génère une palette de couleurs à partir d'une image ou d'une URL
        
        :param colors: Nombre de couleurs à extraire (entre 3 et 10)
        :param url: URL de l'image
        :param file: Image attachée
        :param user: Utilisateur dont l'avatar sera utilisé
        """
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel)):
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un salon écrit", ephemeral=True)
        
        await interaction.response.defer()
        img = None
        if file:
            img = BytesIO(await file.read()) # type: ignore
        elif url:
            img = BytesIO(requests.get(url).content)
        elif user:
            img = BytesIO(await user.display_avatar.read())
        else:
            # On récupère la dernière image envoyée sur le salon (parmi les 10 derniers messages)
            async for message in interaction.channel.history(limit=10):
                if message.attachments:
                    img = BytesIO(await message.attachments[0].read())
                    break
        if not img:
            return await interaction.followup.send("**Erreur** · Aucune image n'a été trouvée dans les derniers messages ni n'a été fournie", ephemeral=True)
        
        try:
            palette = self.draw_image_palette(img, colors)
        except Exception as e:
            logger.error(f'Erreur lors de la génération de la palette : {e}', exc_info=True)
            return await interaction.followup.send('**Erreur** · Impossible de générer la palette de couleurs', ephemeral=True)
        
        with BytesIO() as buffer:
            palette.save(buffer, format='PNG')
            buffer.seek(0)
            await interaction.followup.send(file=discord.File(buffer, 'palette.png'))
            
    rolecolor_group = app_commands.Group(name='color', description='Gestion de votre rôle de couleur', guild_only=True)
    
    @rolecolor_group.command(name='get')
    @app_commands.rename(color='couleur')
    async def _get_color(self, interaction: Interaction, color: str):
        """Obtenir un rôle de la couleur donnée
        
        :param color: Couleur au format hexadécimal (ex. #FF0123)
        """
        member = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        if not self.is_enabled(guild):
            return await interaction.response.send_message("**Erreur** · Le système de rôles de couleurs n'est pas activé sur ce serveur", ephemeral=True)
        
        if self.is_role_limited(member):
            return await interaction.response.send_message("**Erreur** · Vous n'avez pas le droit de changer de couleur car la modération a décidé de limiter cette fonctionnalités à certains membres", ephemeral=True)
        
        if not re.match(r'^#?[0-9A-F]{6}$', color, re.IGNORECASE):
            return await interaction.response.send_message("**Erreur** · La couleur doit être au format hexadécimal (ex. #FF0123)", ephemeral=True)
        
        color = color.lstrip('#')
        if color == DISCORD_INVALID_COLOR:
            return await interaction.response.send_message("**Erreur** · Cette couleur n'est pas valide car utilisée pour les rôles transparents par Discord", ephemeral=True)
        
        role = self.get_user_color_role(member)
        if role:
            try:
                await member.remove_roles(role, reason=f'Rôle de couleur demandé par {member}')
            except discord.Forbidden:
                return await interaction.response.send_message("**Erreur** · Je n'ai pas la permission de modifier tes rôles", ephemeral=True)
            except discord.HTTPException:
                return await interaction.response.send_message("**Erreur** · Une erreur est survenue lors de la modification de tes rôles", ephemeral=True)
        
        new_role = await self.fetch_role(guild, color, requested_by=member)
        try:
            await member.add_roles(new_role, reason=f'Rôle de couleur demandé par {member}')
        except discord.Forbidden:
            return await interaction.response.send_message("**Erreur** · Je n'ai pas la permission de modifier tes rôles", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("**Erreur** · Une erreur est survenue lors de la modification de tes rôles", ephemeral=True)
        
        text = f'Vous avez obtenu le rôle de couleur {new_role.mention}'
        if not self.is_color_displayed(member):
            text += f'\n**Note ·** Si vous ne voyez pas la couleur, vérifiez que vous ne possédez pas un autre rôle coloré plus haut dans la liste de vos rôles'
        em = discord.Embed(title='Rôle de couleur', description=f'Vous avez obtenu le rôle de couleur {new_role.mention}', color=new_role.color)
        
        em.set_thumbnail(url=self.get_color_block(color))
        em.set_footer(text=self.get_color_name(color) or 'Nom inconnu')
        await interaction.response.send_message(embed=em, ephemeral=True)
        
    @rolecolor_group.command(name='remove')
    async def _remove_color(self, interaction: Interaction):
        """Retirer vos rôles de couleurs gérés par le bot"""
        member = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        if not self.is_enabled(guild):
            return await interaction.response.send_message("**Erreur** · Le système de rôles de couleurs n'est pas activé sur ce serveur", ephemeral=True)
        
        await interaction.response.defer()
        roles = [r for r in member.roles if r.name.startswith('#') and r.name[1:].isalnum()]
        if not roles:
            return await interaction.followup.send("**Erreur** · Vous n'avez aucun rôle de couleur", ephemeral=True)
        
        try:
            await member.remove_roles(*roles, reason=f'Rôles de couleur retirés par {member}')
        except discord.Forbidden:
            return await interaction.followup.send("**Erreur** · Je n'ai pas la permission de modifier tes rôles", ephemeral=True)
        
        await self.clean_roles(guild)
        await interaction.followup.send('**Succès** · Vos rôles de couleur ont été retirés', ephemeral=True)
        
    @rolecolor_group.command(name='switcher')
    @app_commands.rename(enabled='activer')
    async def _auto_color_switcher(self, interaction: Interaction, enabled: bool):
        """Activer ou désactiver le changement automatique de couleur lorsque vous changez votre avatar
        
        :param enabled: Activer ou désactiver sur tous les serveurs
        """
        member = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        if not self.is_enabled(guild):
            return await interaction.response.send_message("**Erreur** · Le système de rôles de couleurs n'est pas activé sur ce serveur", ephemeral=True)
        
        if self.is_role_limited(member):
            return await interaction.response.send_message("**Erreur** · Vous n'avez pas le droit de changer de couleur car la modération a décidé de limiter cette fonctionnalités à certains membres", ephemeral=True)
        
        self.set_autoswitch(member, enabled)
        await interaction.response.send_message(f"**Paramètre modifié** · Le changement automatique de couleur a été {'activé' if enabled else 'désactivé'}", ephemeral=True)

    @rolecolor_group.command(name='auto')
    async def _auto_avatar_color(self, interaction: Interaction):
        """Applique la couleur dominante de votre avatar comme couleur de rôle"""
        member = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        if not self.is_enabled(guild):
            return await interaction.response.send_message("**Erreur** · Le système de rôles de couleurs n'est pas activé sur ce serveur", ephemeral=True)
        
        if self.is_role_limited(member):
            return await interaction.response.send_message("**Erreur** · Vous n'avez pas le droit de changer de couleur car la modération a décidé de limiter cette fonctionnalités à certains membres", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        colors = await self.get_avatar_colors(member, count=1)
        if colors:
            role = await self.fetch_role(guild, colors[0], requested_by=member)
            current_role = self.get_user_color_role(member)
            if current_role:
                try:
                    await member.remove_roles(current_role, reason=f'Rôle de couleur pour {member}')
                except discord.Forbidden:
                    logger.warning(f'Impossible de retirer le rôle de couleur à {member}')
                except discord.HTTPException:
                    logger.warning(f'Impossible de retirer le rôle de couleur à {member}')
            try:
                await member.add_roles(role, reason=f'Rôle de couleur pour {member}')
            except discord.Forbidden:
                logger.warning(f'Impossible d\'ajouter le rôle de couleur automatique à {member}')
            except discord.HTTPException:
                logger.warning(f'Impossible d\'ajouter le rôle de couleur automatique à {member}')
            em = discord.Embed(title='Rôle de couleur', description=f'Vous avez obtenu le rôle de couleur {role.mention}', color=role.color)
            em.set_thumbnail(url=self.get_color_block(colors[0]))
            em.set_footer(text=self.get_color_name(colors[0]) or 'Nom inconnu')
            await interaction.followup.send(embed=em, ephemeral=True)
        else:
            await interaction.followup.send('**Erreur** · Impossible de récupérer la couleur de votre avatar, essayez `/palette`', ephemeral=True)

    managecolor_group = app_commands.Group(name='configcolors', description='Commandes de modération des rôles de couleur', guild_only=True, default_permissions=discord.Permissions(administrator=True))
        
    @managecolor_group.command(name='enable')
    @app_commands.rename(enabled='activer')
    async def _enable_colors(self, interaction: Interaction, enabled: bool):
        """Activer ou désactiver le système de rôles de couleurs sur le serveur"""
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        self.data.update_settings(guild, {'Enabled': int(enabled)})
        await interaction.response.send_message(f"**Paramètre modifié** · Le système de rôles de couleurs a été {'activé' if enabled else 'désactivé'}", ephemeral=True)
        
    @managecolor_group.command(name='boundary')
    @app_commands.rename(role='rôle')
    async def _set_boundary_role(self, interaction: Interaction, role: discord.Role | None):
        """Définir le rôle servant de repère pour le rangement des rôles de couleurs
        
        :param role: Rôle servant de repère
        """
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        self.set_boundary_role(guild, role)
        await interaction.response.send_message(f"**Paramètre modifié** · Le rôle servant de repère a été défini sur {role.mention if role else '`aucun`'}", ephemeral=True)
        
    @managecolor_group.command(name='limit')
    @app_commands.rename(role='rôle')
    async def _set_limit_to_role(self, interaction: Interaction, role: discord.Role | None):
        """Définir le rôle limitant l'utilisation des rôles de couleurs aux membres le possédant
        
        :param role: Rôle limitant l'utilisation des rôles de couleurs
        """
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        self.set_limit_to_role(guild, role)
        await interaction.response.send_message(f"**Paramètre modifié** · Le rôle limitant l'utilisation des rôles de couleurs a été défini sur {role.mention if role else '`aucun`'}", ephemeral=True)
        
    @managecolor_group.command(name='reorder')
    async def _reorder_colors(self, interaction: Interaction):
        """Range tous les rôles de couleurs en dessous du rôle servant de repère"""
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        await interaction.response.defer()
        await self.bulk_move_roles(guild)
        await interaction.followup.send('**Succès** · Les rôles de couleurs ont été rangés', ephemeral=True)
        
    @managecolor_group.command(name='clean')
    async def _clean_colors(self, interaction: Interaction):
        """Supprime tous les rôles de couleurs inutilisés"""
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("**Erreur** · Cette commande ne peut pas être utilisée en dehors d'un serveur", ephemeral=True)
        
        await interaction.response.defer()
        await self.clean_roles(guild)
        await interaction.followup.send('**Succès** · Les rôles de couleurs inutilisés ont été supprimés', ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(Colors(bot))
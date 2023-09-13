# Ce module est essentiel dans le fonctionnement du bot et ne doit pas être supprimé

import io
import logging
import textwrap
import traceback
from contextlib import redirect_stdout
from typing import Any, Optional
import discord

from discord.ext import commands
from discord import app_commands

from common.utils import fuzzy, pretty

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

class HelpMenuView(discord.ui.View):
    """Menu d'aide des commandes"""
    def __init__(self, cog: 'Core', original_interaction: discord.Interaction, *, start_at: str | None, timeout: float | None = 120):
        super().__init__(timeout=timeout)
        self.__cog = cog
        self.__interaction = original_interaction
        self.__start_at = start_at
        
        self.commands : dict[str, list[app_commands.Command | app_commands.Group]] = cog._get_bot_commands()
        self.pages = self.__build_pages()
        self.current_page = 0
        self.message: discord.Message | None = None
        
    def __build_pages(self) -> list[discord.Embed]:
        pages = []
        self.commands = {k: v for k, v in sorted(self.commands.items(), key=lambda c: c[0])}
        for cog_name, commands in self.commands.items():
            commands = sorted(commands, key=lambda c: c.qualified_name)
            cog = self.__cog.bot.get_cog(cog_name)
            if not cog:
                continue
            embed = discord.Embed(title=f"Aide pour les commandes • `{cog.qualified_name}`", color=0x2b2d31)
            text = f"*{cog.description}*\n_ _\n"
            for command in commands:
                if isinstance(command, app_commands.Group):
                    chunk = ''
                    for subcommand in command.commands:
                        if subcommand.qualified_name == self.__start_at:
                            chunk += f"- **`/{subcommand.qualified_name}` - {subcommand.description}**\n"
                        else:
                            chunk += f"- `/{subcommand.qualified_name}` - {subcommand.description}\n"
                    embed.add_field(name=command.qualified_name, value=chunk, inline=False)
                elif isinstance(command, app_commands.ContextMenu):
                    if command.qualified_name == self.__start_at:
                        text += f"- **`Applications > {command.qualified_name}` - {command.description}**\n"
                    else:
                        text += f"- `Applications > {command.qualified_name}` - {command.description}\n"
                else:
                    if command.qualified_name == self.__start_at:
                        text += f"- **`/{command.qualified_name}` - {command.description}**\n"
                    else:
                        text += f"- `/{command.qualified_name}` - {command.description}\n"
            embed.description = text
            embed.set_footer(text=f"Module {len(pages) + 1}/{len(self.commands)} • Testez les commandes pour plus d'infos sur les arguments")
            pages.append(embed)
        return pages
            
    async def start(self):
        """Démarre le menu d'aide"""
        if self.__start_at:
            for command in self.commands.values():
                for c in command:
                    if isinstance(c, app_commands.Group):
                        for subcommand in c.commands:
                            if subcommand.qualified_name == self.__start_at:
                                self.current_page = list(self.commands.values()).index(command)
                                break
                    elif c.qualified_name == self.__start_at:
                        self.current_page = list(self.commands.values()).index(command)
                        break
        embed = self.pages[self.current_page]
        self.message = await self.__interaction.followup.send(embed=embed, view=self)
            
    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Page précédente"""
        self.current_page = self.current_page - 1 if self.current_page > 0 else len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        
    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Page suivante"""
        self.current_page = self.current_page + 1 if self.current_page < len(self.pages) - 1 else 0
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        
    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ferme le menu"""
        self.clear_items()
        if self.message:
            await self.message.delete()
        self.stop()
    
    async def on_timeout(self):
        """Appelé lorsque le menu expire"""
        self.clear_items()
        if self.message:
            await self.message.delete()
        self.stop()

class Core(commands.Cog):
    """Module central du bot, contenant des commandes de base."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_result: Optional[Any] = None

    # ---- Gestion des commandes et modules ----

    @commands.command(name="load", hidden=True)
    @commands.is_owner()
    async def load(self, ctx, *, cog: str):
        """Command which Loads a Module.
        Remember to use dot path. e.g: cogs.owner"""
        try:
            await self.bot.load_extension(cog)
        except Exception as exc:
            await ctx.send(f"**`ERROR:`** {type(exc).__name__} - {exc}")
        else:
            await ctx.send("**`SUCCESS`**")

    @commands.command(name="unload", hidden=True)
    @commands.is_owner()
    async def unload(self, ctx, *, cog: str):
        """Command which Unloads a Module.
        Remember to use dot path. e.g: cogs.owner"""

        try:
            await self.bot.unload_extension(cog)
        except Exception as exc:
            await ctx.send(f"**`ERROR:`** {type(exc).__name__} - {exc}")
        else:
            await ctx.send("**`SUCCESS`**")

    @commands.command(name="reload", hidden=True)
    @commands.is_owner()
    async def reload(self, ctx, *, cog: str):
        """Command which Reloads a Module.
        Remember to use dot path. e.g: cogs.owner"""

        try:
            await self.bot.reload_extension(cog)
        except Exception as exc:
            await ctx.send(f"**`ERROR:`** {type(exc).__name__} - {exc}")
        else:
            await ctx.send("**`SUCCESS`**")

    @commands.command(name="extensions", hidden=True)
    @commands.is_owner()
    async def extensions(self, ctx):
        for ext_name, _ext in self.bot.extensions.items():
            await ctx.send(ext_name)

    @commands.command(name="cogs", hidden=True)
    @commands.is_owner()
    async def cogs(self, ctx):
        for cog_name, _cog in self.bot.cogs.items():
            await ctx.send(cog_name)
            
    # ---- Commandes d'évaluation de code ----
            
    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')
            
    @commands.command(name='eval', hidden=True)
    @commands.is_owner()
    async def eval_code(self, ctx: commands.Context, *, body: str):
        """Evalue du code"""

        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')
                
    # ---- Commandes d'aide des commandes ----
    
    def _get_bot_commands(self):
        cogs = self.bot.cogs
        modules = {}
        for cog_name, cog in cogs.items():
            modules[cog_name] = []
            for command in cog.get_app_commands():
                modules[cog_name].append(command)
        return modules
    
    @app_commands.command(name="help")
    @app_commands.rename(command='commande')
    async def help(self, interaction: discord.Interaction, command: str | None):
        """Affiche de l'aide
        
        :param command: Nom d'une commande spécifique à afficher
        """
        await interaction.response.defer()
        view = HelpMenuView(self, interaction, start_at=command)
        await view.start()
        
    @help.autocomplete('command')
    async def autocomplete_command(self, interaction: discord.Interaction, current: str):
        lcoms = self.bot.tree.get_commands()
        all_commands = []
        for command in lcoms:
            if isinstance(command, app_commands.Group):
                all_commands.extend(command.commands)
            else:
                all_commands.append(command)
        r = fuzzy.finder(current, all_commands, key=lambda c: c.qualified_name)
        return [app_commands.Choice(name=c.qualified_name, value=c.qualified_name) for c in r][:5]

async def setup(bot):
    await bot.add_cog(Core(bot))

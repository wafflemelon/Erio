# Global ban cog for Tuxedo

import discord
from discord.ext import commands
import rethinkdb as r 
from utils import permissions
import argparse
import aiohttp

class DiscordArgparseError(Exception):
    pass


class DiscordArgparseMessage(DiscordArgparseError):
    pass


class DiscordFriendlyArgparse(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        raise DiscordArgparseMessage(f'```\n{message}\n```')

    def error(self, message):
        raise DiscordArgparseError(f'```\n{self.format_usage()}\nerror: {message}\n```')

class GbanException(Exception):
    pass

class Gbans:
    def __init__(self, bot):
        self.conn = bot.conn
        self.bot = bot
        self.token = bot.config.get('GBANS_TOKEN')
        @bot.listen('on_member_join')
        async def on_member_join(u):
            g = u.guild
            exists = (lambda: list(r.table('settings').filter(
                lambda a: a['guild'] == str(g.id)).run(self.conn)) != [])()
            if not exists:
                return
            # we know the guild has an entry in the settings
            settings = list(r.table('settings').filter(
                lambda a: a['guild'] == str(g.id)).run(self.conn))[0]
            if "global_bans" not in settings.keys():
                return
            if not settings['global_bans']:
                return
            try:
                if await self.is_gbanned(u.id):
                    nomsg = False
                    try:
                        details = self.gban_details(u.id)
                        mod = await self.get_user(int(details['mod']))
                        modstr = f'**{mod.name}**#{mod.discriminator} ({mod.id})'
                        msg = await u.send(f'''
**You were banned automatically from {g}.**
The reason for this was that you are globally banned.
The mod that banned you was {modstr}. Contact them for further info.
You were banned for `{details['reason']}` with proof `{details['proof']}`.
                        ''')
                    except discord.Forbidden:
                        nomsg = True
                    await u.ban(reason='[Automatic - user globally banned]')
            except discord.Forbidden:
                if nomsg:
                    return
                else:
                    await msg.delete()

    async def get_user(self, uid: int):
        user = None  # database fetch
        if user is not None:
            # noinspection PyProtectedMember
            return discord.User(state=self.bot._connection, data=user)  # I'm sorry Danny

        user = self.bot.get_user(uid)
        if user is not None:
            return user

        try:
            user = await self.bot.get_user_info(uid)
        except discord.NotFound:
            user = None
        if user is not None:  # intentionally leaving this at the end so we can add more methods after this one
            return user
    
    async def ban(self, uid:int, mod:int, reason:str='<none specified>', proof:str='<none specified>'):
        'Easy interface with the global banner'
        if await self.is_gbanned(uid):
            raise GbanException(f'ID {uid} is already globally banned.')
        r.table('gbans').insert({
            'user': str(uid),
            'mod': str(mod),
            'proof': proof,
            'reason': reason
        }, conflict='update').run(self.conn)
        async with aiohttp.ClientSession().put(f'https://api-pandentia.qcx.io/discord/global_bans/{uid}', headers={'Authorization': self.token},
                                               json={'moderator': mod, 'reason': reason, 'proof': proof}) as resp:
            if resp.status == 403:
                raise GbanException(f'Uh-oh, the API returned Forbidden. Check your token.')
            elif resp.status == 409:
                raise GbanException(f'This user is already remotely banned. They have been banned locally.')

        print(f'[Global bans] {mod} has just banned {uid} globally for {reason} with proof {proof}')

    async def unban(self, uid:int):
        'Easy interface with the global banner'
        if not await self.is_gbanned(uid):
            raise GbanException(f'ID {uid} wasn\'t globally banned.')
        r.table('gbans').filter({'user': str(uid)}).delete().run(self.conn)
        async with aiohttp.ClientSession().delete(f'https://api-pandentia.qcx.io/discord/global_bans/{uid}', headers={'Authorization': self.token}) as resp:
            if resp.status == 403:
                raise GbanException(f'Uh-oh, the API returned Forbidden. Check your token.')
        print(f'[Global bans] {uid} just got globally unbanned')
    
    async def is_gbanned(self, user:int):
        try:
            meme = r.table('gbans').filter({'user': str(user)}).run(self.conn).next()
            return True # is gbanned
        except Exception: # local then remote
            async with aiohttp.ClientSession().get(f'https://api-pandentia.qcx.io/discord/global_bans/{user}') as resp:
                if resp.status == 200:
                    return True
                else:
                    return False

    def gban_details(self, user:int):
        try:
            meme = r.table('gbans').filter({'user': str(user)}).run(self.conn).next()
            return meme
        except Exception:
            return None

    @commands.group(name='gban', aliases=['gbans', 'globalbans', 'global_bans'], invoke_without_command=True)
    async def gban(self, ctx, param):
        raise commands.errors.MissingRequiredArgument()

    @gban.command(aliases=['new', 'ban'])
    @permissions.owner_or_gmod()
    async def add(self, ctx, *args):
        parser = DiscordFriendlyArgparse(prog=ctx.command.name, add_help=True)
        parser.add_argument('-u', '--users', nargs='+', type=int, metavar='ID', required=True, help='List of users to ban.')
        parser.add_argument('-r', '--reason', help='A reason for the ban.')
        parser.add_argument('-p', '--proof', help='A proof for the ban.')
        try:
            args = parser.parse_args(args)
        except DiscordArgparseError as e:
            return await ctx.send(str(e))
        reason = args.reason if args.reason != None else '<no reason specified>'
        proof = args.proof if args.proof != None else '<no proof specified>'
        if args.reason == None and args.proof == None:
            return await ctx.send('Specify either a reason or proof.')
        for uid in args.users:
            try:
                await self.ban(uid, ctx.author.id, reason, proof)
            except GbanException as e:
                return await ctx.send(f':x: {e}')
        await ctx.send(f'User(s) banned for reason `{reason}` with proof `{proof}`.')

    @gban.command(aliases=['rm', 'delete', 'unban'])
    @permissions.owner_or_gmod()
    async def remove(self, ctx, *args):
        parser = DiscordFriendlyArgparse(prog=ctx.command.name, add_help=True)
        parser.add_argument('-u', '--users', nargs='+', type=int, metavar='ID', required=True, help='List of users to unban.')
        try:
            args = parser.parse_args(args)
        except DiscordArgparseError as e:
            return await ctx.send(str(e))
        for uid in args.users:
            try:
                await self.unban(uid)
            except GbanException as e:
                return await ctx.send(f':x: {e}')
        
        await ctx.send(f'User(s) unbanned successfully.')



def setup(bot):
    bot.add_cog(Gbans(bot))

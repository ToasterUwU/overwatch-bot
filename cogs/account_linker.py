import asyncio
import datetime
import urllib.parse
from typing import Dict, List

import aiohttp
import nextcord
from nextcord.ext import commands, tasks
from nextcord.interactions import Interaction

from internal_tools.configuration import CONFIG, JsonDictSaver
from internal_tools.discord import *
from internal_tools.general import error_webhook_send

REGION_ROUTER = {
    "Europe": "eu",
    "USA": "us",
    "Asia": "asia",
}
REGION_ROUTER_REVERSE = {v: k for k, v in REGION_ROUTER.items()}


class HeroClassEnum:
    DPS = "DPS"
    SUPPORT = "SUPPORT"
    TANK = "TANK"


API_ROLE_TO_CLASS = {
    "tank": HeroClassEnum.TANK,
    "damage": HeroClassEnum.DPS,
    "support": HeroClassEnum.SUPPORT,
}


def hex_to_color(hex_color: str):
    return nextcord.Color(int(hex_color.replace("#", ""), 16))


class AccountLinkModal(nextcord.ui.Modal):
    def __init__(self, cog: "AccountLinker"):
        self.cog = cog

        super().__init__(
            "Enter your Account name: ",
            timeout=None,
            custom_id="AccountLinkModal",
        )

        self.account_name_input = nextcord.ui.TextInput(
            "Account Name",
            custom_id="AccountLinkModal:account_name",
            min_length=6,
            required=True,
            placeholder="ToasterUwU#8527",
        )
        self.add_item(self.account_name_input)

    async def callback(self, interaction: Interaction):
        if not interaction.user:
            await interaction.send(
                "Something went wrong, please try again.", ephemeral=True
            )
            return

        if self.account_name_input.value is None:
            raise Exception("No value given, cant proceed")

        if (
            self.account_name_input.value.count("#") != 1
            or not self.account_name_input.value.split("#")[1].isnumeric()
        ):  # type: ignore
            await interaction.send(
                "You forgot to add the # + numbers part, or you put too many of them.\n"
                "Enter your full name, make sure the capitalization is right and that you include all numbers.\n\n"
                "Example:\n"
                "- ToasterUwU#8527 - Correct\n"
                "- ToasterUwU8527 - Wrong\n"
                "- ToasterUwU - Wrong\n"
                "- toasteruwu#8527 - Wrong\n",
                ephemeral=True,
            )
            return

        success = await self.cog.add_account(
            user_id=interaction.user.id,
            account_name=self.account_name_input.value.replace(" ", ""),  # type: ignore
        )

        text = f"You are now entered as '{self.account_name_input.value}'. "
        if success:
            text += "Adding your Roles was successful."
        else:
            text += "\nAdding your Roles was NOT successful, this might be a temporary issue, or you might have entered your name wrong or didnt make your profile public yet.\nRemember that the servers can take up to an hour to notice you setting your profile to public.\nYou DONT have to retry adding your name (if you selected the right one), since the Bot saves it and will automatically retry later."

        await interaction.send(
            text,
            ephemeral=True,
        )

        self.stop()


class AccountLinkMenu(nextcord.ui.View):
    def __init__(self, cog: "AccountLinker"):
        self.cog = cog

        super().__init__(timeout=None)

    @nextcord.ui.button(
        label="Link Account Now",
        custom_id="AccountLinkMenu:button",
        style=nextcord.ButtonStyle.primary,
    )  # type: ignore
    async def open_modal_button(
        self, button: nextcord.Button, interaction: nextcord.Interaction
    ):
        await interaction.response.send_modal(AccountLinkModal(self.cog))


class AccountLinker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.accounts = JsonDictSaver("linked_accounts")
        self.overwatch_roles = JsonDictSaver("overwatch_roles")
        self.notifications = JsonDictSaver(
            "notifications",
            default={"CAREER_PROFILE_PRIVATE": {}, "AUTOMATIC_ROLES": {}},
        )

        self.reported_unknown_heroes = set()
        self.hero_renames: Dict[str, str] = {}

        self.migrate_role_config()
        self.migrate_role_ids()
        self.migrate_linked_accounts()

    def migrate_role_config(self):
        """
        Only top level config keys get merged from the default config, so everything
        role related is synced from the default config (the one in the repo) manually.
        Heroes that got renamed are recognized by their API name.
        """
        default_config = JsonDictSaver("ACCOUNT_LINKER", data_type="config/default")
        config = CONFIG["ACCOUNT_LINKER"]
        heroes = config["HEROES"]

        changed = False
        for hero, default_vals in default_config["HEROES"].items():
            if hero not in heroes:
                old_name = next(
                    (
                        name
                        for name, vals in heroes.items()
                        if name not in default_config["HEROES"]
                        and vals.get("API_NAME") == default_vals["API_NAME"]
                    ),
                    None,
                )
                if old_name is None:
                    heroes[hero] = dict(default_vals)
                    changed = True
                    continue

                heroes[hero] = heroes.pop(old_name)
                self.hero_renames[old_name] = hero
                changed = True

            for key in ["COLOR", "CLASS", "API_NAME"]:
                if heroes[hero].get(key) != default_vals[key]:
                    heroes[hero][key] = default_vals[key]
                    changed = True

        for key in ["SEPERATOR_ROLE_NAMES", "SEPERATOR_ROLE_COLOR", "CLASS_ROLES"]:
            if config[key] != default_config[key]:
                config[key] = default_config[key]
                changed = True

        if changed:
            config.save()

    def migrate_role_ids(self):
        """
        Keep the existing roles of renamed heroes, instead of creating new ones.
        """
        changed = False
        for old_name, new_name in self.hero_renames.items():
            for key in ["MAIN_ROLE_IDS", "HERO_ROLE_IDS"]:
                role_ids = self.overwatch_roles.get(key, {})
                if old_name in role_ids:
                    role_ids[new_name] = role_ids.pop(old_name)
                    changed = True

        if changed:
            self.overwatch_roles.save()

    def migrate_linked_accounts(self):
        """
        Stats are no longer fetched per platform, so the stored platform is obsolete.
        """
        changed = False
        for vals in self.accounts.values():
            if "platform" in vals:
                del vals["platform"]
                changed = True

        if changed:
            self.accounts.save()

    async def create_main_role(self, guild: nextcord.Guild, hero: str, vals: dict):
        main_role = await guild.create_role(
            name=f"{hero} Main",
            color=hex_to_color(vals["COLOR"]),
            hoist=True,
            mentionable=True,
        )

        self.overwatch_roles["MAIN_ROLE_IDS"][hero] = main_role.id
        return main_role

    async def create_hero_role(self, guild: nextcord.Guild, hero: str, vals: dict):
        hero_role = await guild.create_role(
            name=f"{hero}",
            color=hex_to_color(vals["COLOR"]),
        )

        self.overwatch_roles["HERO_ROLE_IDS"][hero] = hero_role.id
        return hero_role

    async def migrate_hero_roles(self, guild: nextcord.Guild):
        """
        Create the roles for heroes that were added to the config after the roles were set up,
        and move them into their section (above the matching seperator role).
        """
        new_main_roles: List[nextcord.Role] = []
        new_hero_roles: List[nextcord.Role] = []
        for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items():
            if hero not in self.overwatch_roles["MAIN_ROLE_IDS"]:
                new_main_roles.append(await self.create_main_role(guild, hero, vals))

            if hero not in self.overwatch_roles["HERO_ROLE_IDS"]:
                new_hero_roles.append(await self.create_hero_role(guild, hero, vals))

        if len(new_main_roles) == 0 and len(new_hero_roles) == 0:
            return

        self.overwatch_roles.save()

        new_role_ids = {r.id for r in new_main_roles + new_hero_roles}
        current_roles = [r for r in await guild.fetch_roles() if not r.is_default()]
        old_positions = {r.id: r.position for r in current_roles}

        # Bottom to top, without the new roles
        ordered_roles = sorted(
            [r for r in current_roles if r.id not in new_role_ids],
            key=lambda r: (r.position, r.id),
        )

        for seperator_key, new_roles in [
            ("TOP_3_SEPERATOR_ROLE_ID", new_main_roles),
            ("OTHER_SEPERATOR_ROLE_ID", new_hero_roles),
        ]:
            seperator_index = next(
                (
                    i
                    for i, r in enumerate(ordered_roles)
                    if r.id == self.overwatch_roles[seperator_key]
                ),
                None,
            )
            if seperator_index is None:
                # Seperator is gone, leave the new roles where Discord put them
                ordered_roles[0:0] = new_roles
                continue

            ordered_roles[seperator_index + 1 : seperator_index + 1] = new_roles

        positions = {
            r: position
            for position, r in enumerate(ordered_roles, start=1)
            if old_positions.get(r.id) != position
        }
        if len(positions) != 0:
            await guild.edit_role_positions(
                positions=positions,  # type: ignore
                reason="Adding roles for new Overwatch Heroes",
            )

    async def sync_role_appearance(self, guild: nextcord.Guild):
        """
        Update names and colors of the existing roles to match the config.
        """
        config = CONFIG["ACCOUNT_LINKER"]

        wanted = []  # (role_id, name, color)
        for hero, role_id in self.overwatch_roles["MAIN_ROLE_IDS"].items():
            if hero in config["HEROES"]:
                wanted.append((role_id, f"{hero} Main", config["HEROES"][hero]["COLOR"]))

        for hero, role_id in self.overwatch_roles["HERO_ROLE_IDS"].items():
            if hero in config["HEROES"]:
                wanted.append((role_id, f"{hero}", config["HEROES"][hero]["COLOR"]))

        for seperator_key, name_key in [
            ("TOP_3_SEPERATOR_ROLE_ID", "TOP_3_USED_HEROES"),
            ("OTHER_SEPERATOR_ROLE_ID", "OTHER_INFOS"),
        ]:
            wanted.append(
                (
                    self.overwatch_roles[seperator_key],
                    config["SEPERATOR_ROLE_NAMES"][name_key],
                    config["SEPERATOR_ROLE_COLOR"],
                )
            )

        for hero_class, role_id in self.overwatch_roles["CLASS_ROLE_IDS"].items():
            if hero_class in config["CLASS_ROLES"]:
                wanted.append(
                    (role_id, f"{hero_class}", config["CLASS_ROLES"][hero_class])
                )

        for role_id, name, hex_color in wanted:
            role = await GetOrFetch.role(guild, role_id)
            if not role:
                continue

            color = hex_to_color(hex_color)
            if role.name != name or role.color != color:
                await role.edit(
                    name=name, color=color, reason="Syncing Overwatch roles with config"
                )

    async def cog_application_command_check(self, interaction: nextcord.Interaction):
        """
        Everyone can use this.
        """
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        channel = await GetOrFetch.channel(
            self.bot, CONFIG["ACCOUNT_LINKER"]["MENU_CHANNEL_ID"]
        )
        if isinstance(channel, nextcord.abc.GuildChannel):
            if isinstance(channel, nextcord.TextChannel):
                async for msg in channel.history(limit=None):
                    await msg.delete()

                with open("assets/ACCOUNT_LINKER/link_account.md", "r") as f:
                    content = f.read()

                await channel.send(
                    content,
                    file=nextcord.File(
                        "assets/ACCOUNT_LINKER/social_settings_screenshot.png"
                    ),
                    view=AccountLinkMenu(self),
                )

            if len(self.overwatch_roles) == 0:
                guild = channel.guild

                # Main Roles
                self.overwatch_roles["MAIN_ROLE_IDS"] = {}
                for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items():
                    await self.create_main_role(guild, hero, vals)

                # Top 3 Seperator role
                top_3_seperator_role = await guild.create_role(
                    name=CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_NAMES"][
                        "TOP_3_USED_HEROES"
                    ],
                    color=hex_to_color(CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_COLOR"]),
                    hoist=True,
                    mentionable=True,
                )
                self.overwatch_roles["TOP_3_SEPERATOR_ROLE_ID"] = (
                    top_3_seperator_role.id
                )

                # Top 3 Hero roles
                self.overwatch_roles["HERO_ROLE_IDS"] = {}
                for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items():
                    await self.create_hero_role(guild, hero, vals)

                # Other Seperator role
                other_seperator_role = await guild.create_role(
                    name=CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_NAMES"][
                        "OTHER_INFOS"
                    ],
                    color=hex_to_color(CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_COLOR"]),
                    hoist=True,
                    mentionable=True,
                )
                self.overwatch_roles["OTHER_SEPERATOR_ROLE_ID"] = (
                    other_seperator_role.id
                )

                # Main Class roles
                self.overwatch_roles["CLASS_ROLE_IDS"] = {}
                for hero_class, color in CONFIG["ACCOUNT_LINKER"][
                    "CLASS_ROLES"
                ].items():
                    class_role = await guild.create_role(
                        name=f"{hero_class}",
                        color=hex_to_color(color),
                    )

                    self.overwatch_roles["CLASS_ROLE_IDS"][hero_class] = class_role.id

                self.overwatch_roles.save()

            else:
                await self.migrate_hero_roles(channel.guild)
                await self.sync_role_appearance(channel.guild)

        self.update_overwatch_roles.start()
        self.remind_about_automatic_roles.start()

    async def assign_overwatch_roles(self, member: nextcord.Member, account_name: str):
        url = f"{CONFIG['ACCOUNT_LINKER']['OVERFAST_API_URL']}/players/{urllib.parse.quote(account_name.replace('#', '-'))}/stats/summary"

        async with aiohttp.ClientSession() as session:
            try:
                resp = await session.get(url)
            except:
                return False

            if resp.status == 404:  # Player not found
                return False

            if not resp.ok:
                await error_webhook_send(
                    f"OverFast API Error ({resp.status}) ( {url} ): {(await resp.text())[:1500]}"
                )
                return False

            data = await resp.json()

            # Private profiles (and profiles without any stats) return an empty result
            if not data.get("heroes"):
                today = datetime.datetime.utcnow()
                if (
                    member.id in self.notifications["CAREER_PROFILE_PRIVATE"]
                    and today - self.notifications["CAREER_PROFILE_PRIVATE"][member.id]
                    > datetime.timedelta(days=3)
                ) or member.id not in self.notifications["CAREER_PROFILE_PRIVATE"]:
                    try:
                        await member.send(
                            "Hello, i tried to fetch your Career Profile to assign you the roles you should have,"
                            " but your Career Profile is private (or has no stats yet) at the moment.\n"
                            "Please make it public again,"
                            " or ask Aki to remove your data from my database so that i wont try to do this again."
                        )
                        self.notifications["CAREER_PROFILE_PRIVATE"][member.id] = today
                        self.notifications.save()
                    except:
                        pass

                return False

            api_name_to_hero = {
                vals["API_NAME"]: hero
                for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items()
            }

            played_amounts: Dict[str, int] = {}
            for api_hero, stats in data["heroes"].items():
                if api_hero not in api_name_to_hero:
                    if api_hero not in self.reported_unknown_heroes:
                        self.reported_unknown_heroes.add(api_hero)
                        await error_webhook_send(f"Unknown Hero `{api_hero}` from API")
                    continue

                played_amounts[api_name_to_hero[api_hero]] = stats["time_played"]

            class_amounts: Dict[str, int] = {
                API_ROLE_TO_CLASS[api_role]: stats["time_played"]
                for api_role, stats in (data.get("roles") or {}).items()
                if api_role in API_ROLE_TO_CLASS and stats
            }

            if len(played_amounts) == 0 or len(class_amounts) == 0:
                return False

            main_hero = max(played_amounts, key=played_amounts.get)  # type: ignore
            del played_amounts[main_hero]

            top_3_heroes = []
            for _ in range(3):
                if len(played_amounts) == 0:
                    break

                key = max(played_amounts, key=played_amounts.get)  # type: ignore
                top_3_heroes.append(key)

                del played_amounts[key]

            most_played_class = max(class_amounts, key=class_amounts.get)  # type: ignore

            roles_to_remove = []
            roles_to_add = []

            role = await GetOrFetch.role(
                member.guild, self.overwatch_roles["TOP_3_SEPERATOR_ROLE_ID"]
            )
            if role:
                if role not in member.roles:
                    roles_to_add.append(role)

            role = await GetOrFetch.role(
                member.guild, self.overwatch_roles["OTHER_SEPERATOR_ROLE_ID"]
            )
            if role:
                if role not in member.roles:
                    roles_to_add.append(role)

            for hero, role_id in self.overwatch_roles["MAIN_ROLE_IDS"].items():
                role = await GetOrFetch.role(member.guild, role_id)
                if role:
                    if main_hero == hero:
                        if role not in member.roles:
                            roles_to_add.append(role)
                    else:
                        if role in member.roles:
                            roles_to_remove.append(role)

            for hero, role_id in self.overwatch_roles["HERO_ROLE_IDS"].items():
                role = await GetOrFetch.role(member.guild, role_id)
                if role:
                    if hero in top_3_heroes:
                        if role not in member.roles:
                            roles_to_add.append(role)
                    else:
                        if role in member.roles:
                            roles_to_remove.append(role)

            for hero_class, role_id in self.overwatch_roles["CLASS_ROLE_IDS"].items():
                role = await GetOrFetch.role(member.guild, role_id)
                if role:
                    if hero_class == most_played_class:
                        if role not in member.roles:
                            roles_to_add.append(role)
                    else:
                        if role in member.roles:
                            roles_to_remove.append(role)

            if len(roles_to_remove) != 0:
                await member.remove_roles(*roles_to_remove)
            if len(roles_to_add) != 0:
                await member.add_roles(*roles_to_add)

            return True

    async def add_account(self, user_id: int, account_name: str):
        self.accounts[user_id] = {
            "account_name": account_name,
        }

        self.accounts.save()

        home_guild = await GetOrFetch.guild(
            self.bot, CONFIG["GENERAL"]["HOME_SERVER_ID"]
        )
        if home_guild:
            member = await GetOrFetch.member(home_guild, user_id)
            if member:
                return await self.assign_overwatch_roles(member, account_name)

        return False

    @tasks.loop(hours=12)
    async def update_overwatch_roles(self):
        home_guild = await GetOrFetch.guild(
            self.bot, CONFIG["GENERAL"]["HOME_SERVER_ID"]
        )
        if home_guild:
            for user_id, vals in self.accounts.items():
                member = await GetOrFetch.member(home_guild, user_id)
                if member:
                    await self.assign_overwatch_roles(member, vals["account_name"])
                    await asyncio.sleep(60)

    @update_overwatch_roles.error
    async def restart_update_overwatch_roles(self, *args):
        await asyncio.sleep(10)

        self.update_overwatch_roles.restart()

    @tasks.loop(hours=24)
    async def remind_about_automatic_roles(self):
        home_guild = await GetOrFetch.guild(
            self.bot, CONFIG["GENERAL"]["HOME_SERVER_ID"]
        )
        if home_guild:
            get_roles_channel = await GetOrFetch.channel(
                home_guild, CONFIG["ACCOUNT_LINKER"]["MENU_CHANNEL_ID"]
            )
            if isinstance(get_roles_channel, nextcord.TextChannel):
                today = datetime.datetime.utcnow()

                for m in home_guild.members:
                    if m.id not in self.accounts and not m.bot:
                        if (
                            m.id in self.notifications["AUTOMATIC_ROLES"]
                            and today - self.notifications["AUTOMATIC_ROLES"][m.id]
                            > datetime.timedelta(days=14)
                        ) or m.id not in self.notifications["AUTOMATIC_ROLES"]:
                            try:
                                await m.send(
                                    "Hello dear Human,\n\n"
                                    "this is just a friendly reminder that you havent setup the automatic roles feature yet.\n"
                                    "These are the Roles that show your most played Hero, the top 3 played ones after that, and which role you prefer.\n\n"
                                    "This is not required, but its neat and it would be neat if you can take the time to do this.\n\n"
                                    f"Go to {get_roles_channel.mention} for more info and a step by step guide. It will only take a few minutes."
                                )
                                self.notifications["AUTOMATIC_ROLES"][m.id] = today
                                self.notifications.save()
                            except:
                                pass

    @remind_about_automatic_roles.error
    async def restart_remind_about_automatic_roles(self, *args):
        await asyncio.sleep(10)

        self.remind_about_automatic_roles.restart()

    async def show_overwatch_profile(
        self, interaction: nextcord.Interaction, member: nextcord.Member
    ):
        if member.id not in self.accounts:
            await interaction.send(
                "That User has not connected their Profile with this Bot.",
                ephemeral=True,
            )
            return

        account_name = self.accounts[member.id]["account_name"]

        await interaction.send(
            f"{account_name}'s Profile with all Stats: https://overwatch.blizzard.com/en-us/career/{account_name.replace('#', '-')}/",
            ephemeral=True,
        )

    @nextcord.user_command(
        "Overwatch Profile",
        contexts=[nextcord.InteractionContextType.guild],
    )
    async def user_see_overwatch_profile(
        self, interaction: nextcord.Interaction, member: nextcord.Member
    ):
        await self.show_overwatch_profile(interaction, member)

    @nextcord.message_command(
        "Overwatch Profile",
        contexts=[nextcord.InteractionContextType.guild],
    )
    async def message_see_overwatch_profile(
        self, interaction: nextcord.Interaction, msg: nextcord.Message
    ):
        await self.show_overwatch_profile(interaction, msg.author)  # type: ignore

    @nextcord.slash_command(
        "clean-overwatch-roles",
        default_member_permissions=nextcord.Permissions(administrator=True),
        contexts=[nextcord.InteractionContextType.guild],
    )
    async def clean_overwatch_roles(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild:
            name_list = [hero for hero in CONFIG["ACCOUNT_LINKER"]["HEROES"]]
            name_list.extend(CONFIG["ACCOUNT_LINKER"]["CLASS_ROLES"])
            name_list.extend(CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_NAMES"].values())

            for r in guild.roles:
                if r.name.replace(" Main", "") in name_list:
                    await r.delete(reason="Cleaning Overwatch Roles")

            self.overwatch_roles.clear()
            self.overwatch_roles.save()

        await interaction.send("Done.", ephemeral=True)


async def setup(bot):
    bot.add_cog(AccountLinker(bot))

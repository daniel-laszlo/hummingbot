import asyncio
from typing import (
    List,
    Any,
)
from decimal import Decimal
import pandas as pd
from os.path import join
from hummingbot.client.settings import (
    GLOBAL_CONFIG_PATH,
    CONF_FILE_PATH,
)
from hummingbot.client.config.global_config_map import global_config_map
from hummingbot.client.config.config_helpers import (
    missing_required_configs,
    save_to_yml
)
from hummingbot.client.config.security import Security
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.pure_market_making import (
    PureMarketMakingStrategyV2
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hummingbot.client.hummingbot_application import HummingbotApplication


no_restart_pmm_keys = ["bid_spread", "ask_spread"]
global_configs_to_display = ["kill_switch_enabled",
                             "kill_switch_rate",
                             "telegram_enabled",
                             "telegram_token",
                             "telegram_chat_id",
                             "send_error_logs"]


class ConfigCommand:
    def config(self,  # type: HummingbotApplication
               key: str = None,
               value: str = None):
        self.app.clear_input()
        if key is None:
            self.list_configs()
            return
        else:
            if key not in self.config_able_keys():
                self._notify("Invalid key, please choose from the list.")
                return
            safe_ensure_future(self._config_single_key(key, value), loop=self.ev_loop)

    def list_configs(self,  # type: HummingbotApplication
                     ):
        columns = ["Key", "  Value"]
        data = [[cv.key, cv.value] for cv in global_config_map.values()
                if cv.key in global_configs_to_display and not cv.is_secure]
        df = pd.DataFrame(data=data, columns=columns)
        self._notify("\nGlobal Configurations:")
        lines = ["    " + line for line in df.to_string(index=False).split("\n")]
        self._notify("\n".join(lines))

        if self.strategy_name is not None:
            data = [[cv.key, cv.value] for cv in self.strategy_config_map.values() if not cv.is_secure]
            df = pd.DataFrame(data=data, columns=columns)
            self._notify(f"\nStrategy Configurations:")
            lines = ["    " + line for line in df.to_string(index=False).split("\n")]
            self._notify("\n".join(lines))

    def config_able_keys(self  # type: HummingbotApplication
                         ) -> List[str]:
        """
        Returns a list of configurable keys - using config command, excluding exchanges api keys
        as they are set from connect command.
        """
        keys = [c.key for c in global_config_map.values() if c.prompt is not None and not c.is_connect_key]
        if self.strategy_config_map is not None:
            keys += [c.key for c in self.strategy_config_map.values() if c.prompt is not None]
        return keys

    async def check_password(self,  # type: HummingbotApplication
                             ):
        password = await self.app.prompt(prompt="Enter your password >>> ", is_password=True)
        if password != Security.password:
            self._notify("Invalid password, please try again.")
            return False
        else:
            return True

    # Make this function static so unit testing can be performed.
    @staticmethod
    def update_running_pure_mm(pure_mm_strategy: PureMarketMakingStrategyV2, key: str, new_value: Any):
        if key == "bid_spread":
            pure_mm_strategy.pricing_delegate.bid_spread = new_value / Decimal("100")
            return True
        elif key == "ask_spread":
            pure_mm_strategy.pricing_delegate.ask_spread = new_value / Decimal("100")
            return True
        return False

    async def _config_single_key(self,  # type: HummingbotApplication
                                 key: str,
                                 input_value):
        """
        Configure a single variable only.
        Prompt the user to finish all configurations if there are remaining empty configs at the end.
        """

        self.placeholder_mode = True
        self.app.hide_input = True

        try:
            config_var, config_map, file_path = None, None, None
            if key in global_config_map:
                config_map = global_config_map
                file_path = GLOBAL_CONFIG_PATH
            elif self.strategy_config_map is not None and key in self.strategy_config_map:
                config_map = self.strategy_config_map
                file_path = join(CONF_FILE_PATH, self.strategy_file_name)
            config_var = config_map[key]
            if input_value is not None:
                self._notify("Please follow the prompt to complete configurations: ")
            await self.prompt_a_config(config_var, input_value=input_value, assign_default=False)
            await self.update_all_secure_configs()
            missings = missing_required_configs(config_map)
            if missings:
                self._notify(f"\nThere are other configuration required, please follow the prompt to complete them.")
            missings = await self._prompt_missing_configs(config_map)
            save_to_yml(file_path, config_map)
            self._notify(f"\nNew configuration saved:")
            self._notify(f"{key}: {str(config_var.value)}")
            for config in missings:
                self._notify(f"{config.key}: {str(config.value)}")
            if isinstance(self.strategy, PureMarketMakingStrategyV2):
                updated = ConfigCommand.update_running_pure_mm(self.strategy, key, config_var.value)
                if updated:
                    self._notify(f"\nThe current {self.strategy_name} strategy has been updated "
                                 f"to reflect the new configuration.")
        except asyncio.TimeoutError:
            self.logger().error("Prompt timeout")
        except Exception as err:
            self.logger().error(str(err), exc_info=True)
        finally:
            self.app.hide_input = False
            self.placeholder_mode = False
            self.app.change_prompt(prompt=">>> ")

    async def _prompt_missing_configs(self,  # type: HummingbotApplication
                                      config_map):
        missings = missing_required_configs(config_map)
        for config in missings:
            await self.prompt_a_config(config)
        if missing_required_configs(config_map):
            return missings + (await self._prompt_missing_configs(config_map))
        return missings

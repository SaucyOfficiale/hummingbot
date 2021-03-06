import asyncio
import logging
import ujson
from typing import (
    Optional,
    List,
    Tuple
)
import aiohttp
from hummingbot.cli.settings import (
    global_config_map
)


class ExchangeRateConversion:
    erc_logger: Optional[logging.Logger] = None
    _erc_shared_instance: "ExchangeRateConversion" = None
    _exchange_rate_config_override: Optional[List[Tuple[str, float, str]]] = None

    @classmethod
    def get_instance(cls) -> "ExchangeRateConversion":
        if cls._erc_shared_instance is None:
            cls._erc_shared_instance = ExchangeRateConversion()
        return cls._erc_shared_instance

    @classmethod
    def logger(cls) -> logging.Logger:
        if cls.erc_logger is None:
            cls.erc_logger = logging.getLogger(__name__)
        return cls.erc_logger

    @classmethod
    def set_global_exchange_rate_config(cls, config: List[Tuple[str, float, str]]):
        if cls._exchange_rate_config_override is None:
            cls._exchange_rate_config_override = config
        else:
            cls._exchange_rate_config_override.clear()
            cls._exchange_rate_config_override.extend(config)

    def __init__(self):
        self.exchange_rate_config = {}
        self.exchange_rate = {}
        self.fetch_exchange_rate_task = None
        self.update_interval = 60.0
        self.started = False
        try:
            if self._exchange_rate_config_override is None:
                exchange_rate_config = global_config_map["exchange_rate_conversion"].value
            else:
                exchange_rate_config = self._exchange_rate_config_override
            self.exchange_rate_config = {e[0]: {"default": e[1], "source": e[2]} for e in exchange_rate_config}
            self.exchange_rate = {k: float(v.get("default", 1.0)) for k, v in self.exchange_rate_config.items()}
        except Exception:
            self.logger().error("Error initiating config for exchange rate conversion.")

    def adjust_token_rate(self, symbol: str, price: float) -> float:
        if not self.started:
            self.start()

        if self.exchange_rate.get(symbol, None) is not None:
            return self.exchange_rate[symbol] * price
        else:
            return price

    async def update_exchange_rates_from_coincap(self, session):
        try:
            async with session.request("GET", "https://api.coincap.io/v2/assets") as resp:
                rates_dict = ujson.loads(await resp.text())
                for rate_obj in rates_dict["data"]:
                    symbol = rate_obj["symbol"]
                    if symbol in self.exchange_rate and self.exchange_rate_config[symbol]["source"] == "COINCAP_API":
                        self.exchange_rate[symbol] = float(rate_obj["priceUsd"])

            # coincap does not include all coins in assets
            async with session.request("GET", "https://api.coincap.io/v2/rates") as resp:
                rates_dict = ujson.loads(await resp.text())
                for rate_obj in rates_dict["data"]:
                    symbol = rate_obj["symbol"]
                    if symbol in self.exchange_rate and self.exchange_rate_config[symbol]["source"] == "COINCAP_API":
                        self.exchange_rate[symbol] = float(rate_obj["rateUsd"])
        except Exception:
            raise

    async def request_loop(self):
        while True:
            loop = asyncio.get_event_loop()
            try:
                async with aiohttp.ClientSession(loop=loop,
                                                 connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
                    await self.update_exchange_rates_from_coincap(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().error(f"Error sending requests.", exc_info=True, extra={"do_not_send": True})

            await asyncio.sleep(self.update_interval)

    def start(self):
        self.stop()
        self.fetch_exchange_rate_task = asyncio.ensure_future(self.request_loop())
        self.started = True

    def stop(self):
        if self.fetch_exchange_rate_task and not self.fetch_exchange_rate_task.done():
            self.fetch_exchange_rate_task.cancel()
        self.started = False

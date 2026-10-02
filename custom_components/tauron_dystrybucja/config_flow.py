"""Config flow for the Tauron Dystrybucja integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import TauronApi, TauronApiError, TauronUnsupportedAreaError
from .const import (
    CONF_CITY_GAID,
    CONF_CITY_NAME,
    CONF_HOUSE_NO,
    CONF_SCAN_INTERVAL,
    CONF_STREET_GAID,
    CONF_STREET_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SEARCH_LENGTH,
)

_LOGGER = logging.getLogger(__name__)


class TauronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Guide the user through city -> street -> house number."""

    VERSION = 2

    def __init__(self) -> None:
        self._api: TauronApi | None = None
        self._cities: dict[str, dict[str, Any]] = {}
        self._streets: dict[str, dict[str, Any]] = {}
        self._city: dict[str, Any] | None = None
        self._street: dict[str, Any] | None = None

    @property
    def api(self) -> TauronApi:
        if self._api is None:
            self._api = TauronApi(async_get_clientsession(self.hass))
        return self._api

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for part of the city name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            part = user_input["city_partial"].strip()
            if len(part) < MIN_SEARCH_LENGTH:
                errors["city_partial"] = "too_few_characters"
            else:
                try:
                    cities = await self.api.async_get_cities(part)
                except TauronApiError as err:
                    _LOGGER.error("Error searching cities: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    if cities:
                        self._cities = {self._city_label(c): c for c in cities}
                        return await self.async_step_city()
                    errors["city_partial"] = "no_cities_found"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("city_partial"): str}),
            errors=errors,
        )

    async def async_step_city(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of the matching cities."""
        if user_input is not None:
            self._city = self._cities[user_input["city"]]
            return await self.async_step_street()

        return self.async_show_form(
            step_id="city",
            data_schema=vol.Schema({vol.Required("city"): vol.In(sorted(self._cities))}),
            last_step=False,
        )

    async def async_step_street(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for part of the street name."""
        assert self._city is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            part = user_input["street_partial"].strip()
            if len(part) < MIN_SEARCH_LENGTH:
                errors["street_partial"] = "too_few_characters"
            else:
                try:
                    streets = await self.api.async_get_streets(self._city["GAID"], part)
                except TauronApiError as err:
                    _LOGGER.error("Error searching streets: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    if streets:
                        self._streets = {
                            (s.get("FullName") or s["Name"]): s for s in streets
                        }
                        return await self.async_step_street_selection()
                    errors["street_partial"] = "no_streets_found"

        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema({vol.Required("street_partial"): str}),
            description_placeholders={"city": self._city["Name"]},
            errors=errors,
            last_step=False,
        )

    async def async_step_street_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the matching streets."""
        if user_input is not None:
            self._street = self._streets[user_input["street"]]
            return await self.async_step_house_number()

        return self.async_show_form(
            step_id="street_selection",
            data_schema=vol.Schema({vol.Required("street"): vol.In(sorted(self._streets))}),
            last_step=False,
        )

    async def async_step_house_number(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the house number and create the entry."""
        assert self._city is not None
        assert self._street is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            house_no = user_input["house_no"].strip()
            if not house_no:
                errors["house_no"] = "invalid_house_number"
            else:
                unique_id = f"{self._city['GAID']}-{self._street['GAID']}-{house_no}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                try:
                    await self.api.async_get_outages(
                        city_gaid=self._city["GAID"],
                        street_gaid=self._street["GAID"],
                        house_no=house_no,
                        from_date="2000-01-01T00:00:00",
                        to_date="2000-01-02T00:00:00",
                    )
                except TauronUnsupportedAreaError:
                    errors["base"] = "unsupported_area"
                except TauronApiError as err:
                    _LOGGER.error("Error validating address: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=(
                            f"{self._city['Name']}, {self._street['Name']} {house_no}"
                        ),
                        data={
                            CONF_CITY_NAME: self._city["Name"],
                            CONF_CITY_GAID: self._city["GAID"],
                            CONF_STREET_NAME: self._street["Name"],
                            CONF_STREET_GAID: self._street["GAID"],
                            CONF_HOUSE_NO: house_no,
                        },
                    )

        return self.async_show_form(
            step_id="house_number",
            data_schema=vol.Schema({vol.Required("house_no"): str}),
            errors=errors,
        )

    @staticmethod
    def _city_label(city: dict[str, Any]) -> str:
        """Disambiguate cities that share a name by appending the district."""
        district = city.get("DistrictName")
        return f"{city['Name']} ({district})" if district else city["Name"]

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> TauronOptionsFlow:
        """Return the options flow handler."""
        return TauronOptionsFlow()


class TauronOptionsFlow(OptionsFlow):
    """Lets the user tune how often the API is polled."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )

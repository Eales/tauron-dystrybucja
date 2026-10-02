"""Shared entity base for Tauron Dystrybucja."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CITY_NAME, CONF_HOUSE_NO, CONF_STREET_NAME, DOMAIN
from .coordinator import TauronOutageCoordinator


class TauronEntity(CoordinatorEntity[TauronOutageCoordinator]):
    """Base entity tying all sensors of one address to a single device."""

    _attr_has_entity_name = True

    @property
    def extra_state_attributes(self):
        """Expose response scope even when no outage was returned."""
        data = self.coordinator.data
        return {
            **{key: data[key] for key in (
                "scope", "outage_list_type", "address_resolved"
            )},
            **(super().extra_state_attributes or {}),
        }

    def __init__(self, coordinator: TauronOutageCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}-{key}"
        data = entry.data
        address = (
            f"{data[CONF_CITY_NAME]}, {data[CONF_STREET_NAME]} {data[CONF_HOUSE_NO]}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Tauron {address}",
            manufacturer="Tauron Dystrybucja",
            model="Wyłączenia prądu",
            configuration_url="https://www.tauron-dystrybucja.pl/wylaczenia/wylaczenia-planowane",
        )

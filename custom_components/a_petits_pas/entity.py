"""Base entity for the Journal à petits pas integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmisgestDataUpdateCoordinator, ChildData


class AmisgestChildEntity(CoordinatorEntity[AmisgestDataUpdateCoordinator]):
    """Base class for entities tied to one child."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AmisgestDataUpdateCoordinator, person_id: str) -> None:
        super().__init__(coordinator)
        self._person_id = person_id

    @property
    def _child(self) -> ChildData | None:
        return self.coordinator.data.children.get(self._person_id)

    @property
    def available(self) -> bool:
        return super().available and self._child is not None

    @property
    def device_info(self) -> DeviceInfo:
        child = self._child
        model = child.child.client_name if child else None
        if child:
            # A child can be linked to more than one daycare (e.g. attends
            # two CPEs), which produces two devices sharing the same
            # person_name -- append the daycare so their entities don't get
            # identical friendly names (HA would otherwise only disambiguate
            # the entity_id with a "_2" suffix, not the displayed name).
            name = f"{child.child.person_name} ({model})" if model else child.child.person_name
        else:
            name = self._person_id
        return DeviceInfo(
            identifiers={(DOMAIN, self._person_id)},
            name=name,
            manufacturer="Amisgest",
            model=model,
            configuration_url="https://app.journalapetitspas.ca/",
        )

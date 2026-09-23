from __future__ import annotations

from app.detectors.address import AddressDetector
from app.detectors.birth_place import BirthPlaceDetector
from app.detectors.card import CardDetector
from app.detectors.cardholder import CardholderDetector
from app.detectors.citizenship import CitizenshipDetector
from app.detectors.cvv_pin import CvvDetector, PinDetector
from app.detectors.dates import DateDetector
from app.detectors.department_code import DepartmentCodeDetector
from app.detectors.driver_license import DriverLicenseDetector
from app.detectors.email import EmailDetector
from app.detectors.fio import FullNameDetector
from app.detectors.inn import InnDetector
from app.detectors.passport import PassportDetector
from app.detectors.passport_issuer import PassportIssuerDetector
from app.detectors.phone import PhoneDetector
from app.detectors.registry import DetectorRegistry


def register_builtin_detectors(registry: DetectorRegistry) -> None:
    """Populated incrementally (spec Phase 3+): each detector is added here once it
    has its own unit tests passing.
    """
    registry.register(EmailDetector())
    registry.register(FullNameDetector())
    registry.register(PhoneDetector())
    registry.register(InnDetector())
    registry.register(CardDetector())
    registry.register(PassportDetector())
    registry.register(DepartmentCodeDetector())
    registry.register(CvvDetector())
    registry.register(PinDetector())
    registry.register(DriverLicenseDetector())
    registry.register(DateDetector())
    registry.register(CardholderDetector())
    registry.register(AddressDetector())
    registry.register(BirthPlaceDetector())
    registry.register(CitizenshipDetector())
    registry.register(PassportIssuerDetector())

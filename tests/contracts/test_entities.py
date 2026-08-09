"""The contract set, and the two rules that refuse a contract at load time."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from watermark.contracts import ContractError, load
from watermark.contracts.loader import DEFAULT_ROOT
from watermark.contracts.model import EntityContract

VALID = {
    "id": "meter_assignment",
    "title": "Which customer a meter belongs to",
    "kind": "reference",
    "owner": "metering-platform",
    "source": "cdc/dms:public.meter_assignment",
    "grain": "meter",
    "key": ["meter_id"],
    "personal_data": True,
    "purpose": "Attributing a reading to the party responsible for it. GDPR Art. 5(1)(b).",
    "scd2": {"valid_from": "valid_from", "valid_to": "valid_to", "tracked": ["customer_id"]},
}


def write(tmp_path: Path, name: str, contract: dict[str, object]) -> Path:
    directory = tmp_path / "entities"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    return tmp_path


class TestTheRealSet:
    def test_the_contracts_in_this_repository_load(self) -> None:
        assert len(load().entities) == 6

    def test_every_yaml_file_is_a_contract(self) -> None:
        """A file in the directory that nothing loads is a contract somebody wrote and nobody
        enforces."""
        files = {path.stem for path in (DEFAULT_ROOT / "entities").glob("*.yaml")}
        assert files == set(load().entities)

    def test_the_erasure_scope_is_derived_not_maintained(self) -> None:
        """Claim 6 has to enumerate everywhere a subject appears. A hand-kept list is right on
        the day it is written; this is right on the day it is read."""
        assert load().personal_data_entities == ("customer", "meter", "meter_assignment", "tariff")

    def test_the_substation_is_deliberately_not_personal_data(self) -> None:
        """Declaring it false is not a shortcut — an erasure that had to reach every table
        would reach none of them properly."""
        assert load()["substation"].personal_data is False


class TestPersonalDataMustDeclareAPurpose:
    """GDPR Art. 5(1)(b). A purpose that is not written down is not specified."""

    def test_a_personal_data_entity_with_no_purpose_does_not_load(self) -> None:
        with pytest.raises(ValueError, match="declares no purpose"):
            EntityContract.model_validate({**VALID, "purpose": None})

    def test_a_blank_purpose_is_not_a_purpose(self) -> None:
        with pytest.raises(ValueError, match="declares no purpose"):
            EntityContract.model_validate({**VALID, "purpose": "   \n  "})

    def test_an_entity_with_no_personal_data_needs_none(self) -> None:
        contract = EntityContract.model_validate({**VALID, "personal_data": False, "purpose": None})
        assert contract.purpose is None

    def test_the_whole_set_fails_rather_than_loading_partially(self, tmp_path: Path) -> None:
        """A partially loaded contract set is worse than none: the checks that run are the ones
        whose contracts happened to parse."""
        root = write(tmp_path, "meter_assignment", {**VALID, "purpose": None})
        with pytest.raises(ContractError, match="declares no purpose"):
            load(root)


class TestAKeyDoesNotChange:
    def test_a_key_field_that_is_also_tracked_is_refused(self) -> None:
        """A history keyed on something that changes is two entities wearing one name, and
        every point-in-time resolution against it returns whichever version sorts first."""
        broken = {
            **VALID,
            "scd2": {
                "valid_from": "valid_from",
                "valid_to": "valid_to",
                "tracked": ["meter_id", "customer_id"],
            },
        }
        with pytest.raises(ValueError, match="two entities wearing one name"):
            EntityContract.model_validate(broken)


class TestCrossChecks:
    def test_a_reference_to_a_missing_entity_fails_the_set(self, tmp_path: Path) -> None:
        """A join written against it compiles, returns nothing, and reads as a customer with no
        consumption."""
        root = write(
            tmp_path,
            "meter_assignment",
            {**VALID, "references": [{"entity": "ghost", "via": "customer_id"}]},
        )
        with pytest.raises(ContractError, match="does not exist"):
            load(root)

    def test_a_reference_on_a_field_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nothing to join on"):
            EntityContract.model_validate(
                {**VALID, "references": [{"entity": "customer", "via": "nope"}]}
            )

    def test_a_non_personal_entity_may_not_reference_a_personal_one(self, tmp_path: Path) -> None:
        """The reach of GDPR follows the join, not the table name. Leaving this in decides that
        the erasure scope is one table short."""
        directory = tmp_path / "entities"
        directory.mkdir()
        write(tmp_path, "meter_assignment", VALID)
        write(
            tmp_path,
            "substation",
            {
                "id": "substation",
                "title": "A substation",
                "kind": "reference",
                "owner": "network-operations",
                "source": "cdc/dms:public.substation",
                "grain": "substation",
                "key": ["substation_id"],
                "personal_data": False,
                "scd2": {
                    "valid_from": "valid_from",
                    "valid_to": "valid_to",
                    "tracked": ["occupant_meter"],
                },
                "references": [{"entity": "meter_assignment", "via": "occupant_meter"}],
            },
        )
        with pytest.raises(ContractError, match="erasure scope is one table short"):
            load(tmp_path)

    def test_the_filename_and_the_id_must_agree(self, tmp_path: Path) -> None:
        """Two names for one thing. When they drift, a reviewer reading the directory sees a
        set that does not exist."""
        root = write(tmp_path, "renamed", VALID)
        with pytest.raises(ContractError, match="declares id"):
            load(root)

    def test_an_unknown_field_is_refused_rather_than_ignored(self) -> None:
        """A typo in a contract key is a rule silently not applied."""
        with pytest.raises(ValueError, match=r"personl_data|Extra inputs"):
            EntityContract.model_validate({**VALID, "personl_data": True})

    def test_a_missing_directory_is_an_error_not_an_empty_set(self, tmp_path: Path) -> None:
        """An empty contract set passes every cross-check there is."""
        with pytest.raises(ContractError, match="no entity contracts"):
            load(tmp_path)

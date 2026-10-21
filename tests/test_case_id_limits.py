"""Case IDs remain intact across generation schemas and preflight summaries."""
from typing import Annotated

import pytest
from pydantic import TypeAdapter, ValidationError
from backend import generation_protocol as protocol, generation_fault_first as fault
from backend import generation_fault_obligations as obligations, generation_roles as roles
from backend.generation_schema import Case, CASE_ID_MAX_LENGTH
from backend.generation_safeguards import Additions


@pytest.mark.parametrize('model,field', [
    (Case, 'id'), (protocol.BehaviorCase, 'id'),
    (protocol.Fingerprint, 'case_id'), (protocol.FieldPatch, 'case_id'),
    (fault.FaultCheck, 'case_id'), (fault.PrimarySelection, 'case_id'),
    (obligations.Impact, 'case_id'),
])
def test_case_id_boundary_consistent(model, field):
    info = model.model_fields[field]
    adapter = TypeAdapter(Annotated.__class_getitem__((str, *info.metadata)))
    for size in (46, 47, 96):
        value = 'a' * size
        assert adapter.validate_python(value) == value
    with pytest.raises(ValidationError):
        adapter.validate_python('a' * 97)
    assert CASE_ID_MAX_LENGTH == 96


@pytest.mark.parametrize('value', ['', '1abc', 'Upper', 'a/b', 'a b', '../abc'])
def test_case_id_character_rules_unchanged(value):
    with pytest.raises(ValidationError):
        Case.model_validate(dict(id=value, group='target', visibility='hidden', covers=['sum'], input={}, expected=0))


def test_append_and_preflight_preserve_long_case_id(monkeypatch):
    value = 'hidden_regression_timeout_after_write_last_attempt'
    case = dict(id=value, group='regression', visibility='hidden', covers=['sum'],
                input={}, expected=0, classification_reason='这是新增的完整边界场景说明，用于验证接口。')
    addition = Additions.model_validate(dict(base_hash='a'*64, added_cases=[case]))
    assert addition.added_cases[0].id == value
    monkeypatch.setattr(roles, 'rejected_candidate', lambda *args: {'cases':[case]})
    summary = roles.preflight_summary({'assets': {'evaluation':'example'}, 'contract': {'behaviors': {'sum':'求和'}}})
    assert summary['coverage']['sum'] == [value]
    assert summary['missing_behaviors'] == []

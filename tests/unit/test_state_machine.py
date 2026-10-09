import pytest
from controlplane.domain.state_machine import can_transition,assert_transition
def test_valid_transition(): assert can_transition('QUEUED','RUNNING')
def test_terminal_is_terminal(): assert not can_transition('SUCCEEDED','RUNNING')
def test_invalid_transition_raises():
    with pytest.raises(ValueError): assert_transition('FAILED','RUNNING')

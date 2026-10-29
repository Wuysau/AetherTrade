"""A claimed SQL bundle or model flag cannot mint the kernel's live witness."""
from types import SimpleNamespace
import pytest


def test_attachment_needs_actual_same_transaction_financial_kernel_witness():
    from trading_system.risk.program_financial_admission import claim_program_attachment
    tx=SimpleNamespace(active=True,transaction_id='current',scope=None)
    with pytest.raises(ValueError,match='PROGRAM_FINANCIAL_WITNESS_REQUIRED'):
        claim_program_attachment(tx,object(),mode='LOCAL_ACTIVATION',command_ids=())


def test_command_identity_getter_has_no_zero_asset_inference_fallback():
    from trading_system.risk.program_financial_admission import program_bundle_command_ids
    tx=SimpleNamespace(active=True,transaction_id='current',scope=None)
    with pytest.raises(ValueError,match='PROGRAM_FINANCIAL_WITNESS_REQUIRED'):
        program_bundle_command_ids(tx,object())

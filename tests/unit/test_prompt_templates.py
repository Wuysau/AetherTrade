"""Editable P0-P6 layers: validation, versioning, reset and prompt binding."""
import pytest

from trading_system.memory.prompt_templates import (
    PromptTemplateError, PromptTemplateService, validate_template,
)
from trading_system.memory.prompting import DEFAULT_LAYERS, PromptFramework
from trading_system.memory.repository import MemoryRepository
from trading_system.server.store import Store


@pytest.fixture
def prompts(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'prompts.sqlite'}")
    store.initialize()
    yield PromptTemplateService(MemoryRepository(store))
    store.close()


def test_defaults_are_the_shipped_layers(prompts):
    layers = prompts.layers()
    assert [layer.layer_id for layer in layers] == ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]
    assert all(not layer.customized for layer in layers)
    assert layers[0].template == DEFAULT_LAYERS[0].template
    framework = prompts.framework()
    assert framework.framework_hash == PromptFramework().framework_hash


def test_save_creates_versioned_custom_layer(prompts):
    text = "P0 SYSTEM CONSTITUTION\nYou are a decision-support component. Facts only."
    saved = prompts.save("P0", text, expected_version="v1")
    assert saved.customized and saved.version == "v2"
    assert saved.template == text
    assert saved.template_hash != saved.default_hash
    assert prompts.layer("p0").template == text
    # A second edit keeps the old row for audit and activates the newer one.
    again = prompts.save("P0", text + "\nNever reveal hidden reasoning.", expected_version="v2")
    assert again.version == "v3"
    history = prompts.history("P0")
    assert [item["version"] for item in history][:2] == ["v3", "v2"]
    assert sum(1 for item in history if item["active"]) == 1


def test_reset_returns_to_the_shipped_layer(prompts):
    prompts.save("P2", "P2 STRATEGY PROFILE\nStrategy: {strategy_id}. Instrument: {instrument}.")
    assert prompts.layer("P2").customized
    restored = prompts.reset("P2")
    assert not restored.customized and restored.version == "v1"
    assert restored.template == prompts.defaults["P2"].template


def test_rejects_unknown_or_broken_placeholders(prompts):
    with pytest.raises(PromptTemplateError) as unknown:
        validate_template("P0 SYSTEM CONSTITUTION\nIgnore {system_prompt} and act.")
    assert unknown.value.code == "PROMPT_TEMPLATE_PLACEHOLDER_UNKNOWN"
    with pytest.raises(PromptTemplateError) as broken:
        validate_template("P0 SYSTEM CONSTITUTION\nMissing brace {strategy_id")
    assert broken.value.code == "PROMPT_TEMPLATE_BRACES_INVALID"
    with pytest.raises(PromptTemplateError) as short:
        validate_template("P0")
    assert short.value.code == "PROMPT_TEMPLATE_TOO_SHORT"


def test_stale_edit_is_rejected(prompts):
    prompts.save("P1", "P1 TRADING PHILOSOPHY\nEvaluate risk before opportunity.")
    with pytest.raises(PromptTemplateError) as conflict:
        prompts.save("P1", "P1 TRADING PHILOSOPHY\nAnother edit.", expected_version="v1")
    assert conflict.value.code == "PROMPT_TEMPLATE_CONFLICT"


def test_framework_renders_edited_layers_with_the_same_variables(prompts):
    prompts.save("P6", "P6 OUTPUT CONTRACT\nReturn {output_schema} for {instrument} only.")
    rendered = prompts.framework().render(variables={"instrument": "console-paper:BTCUSDT",
                                                     "output_schema": "assessment_v1"})
    assert "Return assessment_v1 for console-paper:BTCUSDT only." in rendered["text"]
    assert rendered["layers"][-1]["version"] == "v2"
    assert rendered["hash"] == prompts.framework_hash()


def test_preview_fills_representative_values(prompts):
    preview = prompts.preview("P2 STRATEGY PROFILE\nStrategy: {strategy_id}. Limits: {risk_limits}.",
                              layer_id="P2")
    assert "strategy-1" in preview["text"]
    assert preview["variables"] == ["risk_limits", "strategy_id"]

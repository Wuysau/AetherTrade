"""Operator-editable P0-P6 prompt layers.

The shipped defaults stay in code. A saved edit is an immutable row in
``console_prompt_templates``; exactly one row per layer is active. Rendering and
prompt submission are unchanged: the assembler still builds the same variables
and the server still sends the rendered text as the system instruction.

Editing a layer changes prompt wording only. It cannot grant risk, identity,
permission or execution authority, and it cannot change the variables the
server supplies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import string

from trading_system.contracts.canonical import canonical_hash

from .prompting import (
    DEFAULT_LAYERS, KNOWN_PROMPT_VARIABLES, PROMPT_FRAMEWORK_VERSION,
    PromptFramework, PromptLayer,
)

MAX_TEMPLATE_LENGTH = 12000
MIN_TEMPLATE_LENGTH = 24
_LAYER_IDS = ("P0", "P1", "P2", "P3", "P4", "P5", "P6")
_VERSION = re.compile(r"^v([0-9]+)$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptTemplateError(ValueError):
    """Rejected prompt edit. ``code`` is safe to return to the browser."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True, kw_only=True)
class PromptLayerView:
    layer_id: str
    title: str
    role: str
    version: str
    template: str
    default_template: str
    customized: bool
    variables: tuple[str, ...]
    template_hash: str
    default_hash: str
    updated_at: str | None = None

    def as_payload(self) -> dict:
        return {
            "layer_id": self.layer_id, "title": self.title, "role": self.role,
            "version": self.version, "template": self.template,
            "default_template": self.default_template, "customized": self.customized,
            "variables": list(self.variables), "template_hash": self.template_hash,
            "default_hash": self.default_hash, "updated_at": self.updated_at,
        }


def _parse_placeholders(template: str) -> tuple[str, ...]:
    names: list[str] = []
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        raise PromptTemplateError("PROMPT_TEMPLATE_BRACES_INVALID",
                                  "花括号不匹配。占位符请使用 {变量名} 形式。") from None
    for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if format_spec or conversion or not field_name:
            raise PromptTemplateError("PROMPT_TEMPLATE_PLACEHOLDER_INVALID",
                                      "占位符不支持格式说明或转换，请使用 {变量名}。")
        if field_name not in KNOWN_PROMPT_VARIABLES:
            raise PromptTemplateError(
                "PROMPT_TEMPLATE_PLACEHOLDER_UNKNOWN",
                "未知占位符：{" + field_name + "}。可用变量以编辑器列表为准。")
        names.append(field_name)
    return tuple(sorted(set(names)))


def validate_template(template: object) -> str:
    if not isinstance(template, str):
        raise PromptTemplateError("PROMPT_TEMPLATE_INVALID", "提示词必须是文本。")
    text = template.replace("\r\n", "\n").strip()
    if len(text) < MIN_TEMPLATE_LENGTH:
        raise PromptTemplateError("PROMPT_TEMPLATE_TOO_SHORT", "提示词内容过短。")
    if len(text) > MAX_TEMPLATE_LENGTH:
        raise PromptTemplateError("PROMPT_TEMPLATE_TOO_LARGE",
                                  "单层提示词不能超过 12000 个字符。")
    _parse_placeholders(text)
    return text


class PromptTemplateService:
    """Read effective layers and persist operator edits."""

    def __init__(self, repository, *, defaults=DEFAULT_LAYERS, clock=None):
        self.repository = repository
        self.defaults = {layer.layer_id: layer for layer in defaults}
        if tuple(self.defaults) != _LAYER_IDS:
            raise ValueError("PROMPT_DEFAULTS_INVALID")
        self.clock = clock or _now

    # ---- reads ---------------------------------------------------------

    def _rows(self) -> dict[str, list[dict]]:
        rows: dict[str, list[dict]] = {layer_id: [] for layer_id in _LAYER_IDS}
        for data, template_id, version, active in self.repository.list_prompt_templates():
            layer_id = str(data.get("layer_id") or template_id).upper()
            if layer_id not in rows:
                continue
            rows[layer_id].append({"data": data, "version": version, "active": bool(active)})
        return rows

    def _active_row(self, rows: dict[str, list[dict]], layer_id: str) -> dict | None:
        active = [row for row in rows.get(layer_id, []) if row["active"]]
        if not active:
            return None
        return max(active, key=lambda row: (row["data"].get("updated_at") or "", row["version"]))

    def _view(self, layer_id: str, rows: dict[str, list[dict]]) -> PromptLayerView:
        default = self.defaults[layer_id]
        row = self._active_row(rows, layer_id)
        template = default.template
        version = default.version
        updated_at = None
        if row is not None and isinstance(row["data"].get("template"), str):
            candidate = row["data"]["template"]
            try:
                template = validate_template(candidate)
                version = row["version"]
                updated_at = row["data"].get("updated_at")
            except PromptTemplateError:
                template, version, updated_at = default.template, default.version, None
        layer = PromptLayer(layer_id=layer_id, title=default.title, version=version,
                            template=template, role=default.role)
        return PromptLayerView(
            layer_id=layer_id, title=default.title, role=default.role, version=version,
            template=template, default_template=default.template,
            customized=template != default.template, variables=layer.variables,
            template_hash=layer.template_hash, default_hash=default.template_hash,
            updated_at=updated_at)

    def layers(self) -> tuple[PromptLayerView, ...]:
        rows = self._rows()
        return tuple(self._view(layer_id, rows) for layer_id in _LAYER_IDS)

    def layer(self, layer_id: str) -> PromptLayerView:
        key = self._normalize(layer_id)
        return self._view(key, self._rows())

    def framework(self) -> PromptFramework:
        """Return the effective framework used for the next decision request."""
        layers = tuple(PromptLayer(layer_id=view.layer_id, title=view.title,
                                   version=view.version, template=view.template,
                                   role=view.role) for view in self.layers())
        return PromptFramework(layers, version=PROMPT_FRAMEWORK_VERSION)

    def framework_hash(self) -> str:
        return self.framework().framework_hash

    def history(self, layer_id: str) -> tuple[dict, ...]:
        key = self._normalize(layer_id)
        rows = [row for row in self._rows()[key]]
        rows.sort(key=lambda row: (row["data"].get("updated_at") or "", row["version"]),
                  reverse=True)
        return tuple({
            "version": row["version"],
            "active": row["active"],
            "template_hash": row["data"].get("hash"),
            "updated_at": row["data"].get("updated_at"),
            "customized": bool(row["data"].get("customized")),
            "length": len(row["data"].get("template") or ""),
        } for row in rows)

    # ---- writes --------------------------------------------------------

    def save(self, layer_id: str, template: object, *, expected_version: str | None = None) -> PromptLayerView:
        key = self._normalize(layer_id)
        text = validate_template(template)
        rows = self._rows()
        current = self._view(key, rows)
        if expected_version is not None and expected_version != current.version:
            raise PromptTemplateError("PROMPT_TEMPLATE_CONFLICT",
                                      "该层提示词已被其他页面修改，请刷新后重试。")
        if text == self.defaults[key].template:
            return self.reset(key)
        numbers = [0, 1]
        for row in rows[key]:
            match = _VERSION.match(row["version"])
            if match:
                numbers.append(int(match.group(1)))
        version = "v" + str(max(numbers) + 1)
        updated_at = self.clock()
        layer = PromptLayer(layer_id=key, title=self.defaults[key].title, version=version,
                            template=text, role=self.defaults[key].role)
        try:
            self.repository.replace_prompt_template(
                key, version, layer.role,
                {"layer_id": key, "title": layer.title, "template": text,
                 "hash": layer.template_hash, "role": layer.role,
                 "customized": True, "updated_at": updated_at,
                 "previous_version": current.version},
                expected_version=expected_version,
                fallback_version=self.defaults[key].version)
        except ValueError:
            raise PromptTemplateError("PROMPT_TEMPLATE_CONFLICT",
                                      "该层提示词已被其他页面修改，请刷新后重试。") from None
        return self._view(key, self._rows())

    def reset(self, layer_id: str) -> PromptLayerView:
        key = self._normalize(layer_id)
        self.repository.deactivate_prompt_templates(key)
        return self._view(key, self._rows())

    def preview(self, template: object, *, layer_id: str | None = None) -> dict:
        """Render one draft layer with representative values, for UI preview only."""
        text = validate_template(template)
        key = self._normalize(layer_id) if layer_id else "P0"
        default = self.defaults[key]
        layer = PromptLayer(layer_id=key, title=default.title, version="preview",
                            template=text, role=default.role)
        sample = {
            "strategy_id": "strategy-1", "instrument": "console-paper:BTCUSDT",
            "product": "SPOT", "strategy_version": "3", "decision_mode": "single",
            "authorization_summary": '{"auto_execute": false}',
            "risk_limits": '{"max_position_pct": "0.2", "max_daily_loss": "500"}',
            "memory_completeness": "COMPLETE", "memory_conflicts": "[]",
            "memory_missing": "[]", "output_schema": "assessment_v1",
        }
        try:
            rendered = layer.template.format(**sample)
        except (KeyError, IndexError, ValueError):
            raise PromptTemplateError("PROMPT_TEMPLATE_PLACEHOLDER_INVALID",
                                      "占位符无法渲染，请检查花括号与变量名。") from None
        return {"layer_id": key, "text": rendered, "template_hash": layer.template_hash,
                "variables": list(layer.variables),
                "sample_variables": sample}

    def _normalize(self, layer_id: object) -> str:
        key = str(layer_id or "").strip().upper()
        if key not in self.defaults:
            raise PromptTemplateError("PROMPT_LAYER_UNKNOWN", "未知的提示词层。")
        return key

    def audit_payload(self) -> dict:
        framework = self.framework()
        return {"version": framework.version, "hash": framework.framework_hash,
                "layers": {view.layer_id: view.version for view in self.layers()},
                "customized": [view.layer_id for view in self.layers() if view.customized]}

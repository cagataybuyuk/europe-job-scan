"""Read-only open Shadow DOM inspection. Never grants mutation authority.

Scope paths are observation identities, not approved write selectors. Closed
shadow roots and later steps cannot be certified from the current document.
"""
from __future__ import annotations

import hashlib
import json


SHADOW_FORM_EXTRACTOR = r"""
() => {
  const controls = [], actions = [];
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const ancestry = el => {
    const result = [];
    for (let n = el; n; n = n.parentElement || n.getRootNode().host) result.push(n);
    return result;
  };
  const shown = el => ancestry(el).every(n => {
    const s = getComputedStyle(n);
    return s.display !== 'none' && s.visibility !== 'hidden' && !n.hidden;
  }) && el.getClientRects().length > 0;
  const label = el => {
    const root = el.getRootNode();
    const aria = norm(el.getAttribute('aria-label'));
    if (aria) return aria;
    const ids = norm(el.getAttribute('aria-labelledby')).split(' ').filter(Boolean);
    const linked = ids.map(id => root.getElementById(id)).filter(Boolean);
    if (linked.length) return norm(linked.map(n => n.textContent).join(' '));
    if (el.labels && el.labels.length) return norm(Array.from(el.labels).map(n => n.textContent).join(' '));
    return norm(el.getAttribute('placeholder'));
  };
  const visit = (root, scope) => {
    const all = Array.from(root.querySelectorAll('*'));
    for (const [index, el] of all.entries()) {
      const local = `${el.tagName.toLowerCase()}@${index}`;
      if (el.matches('input,textarea,select') && !['hidden','submit','button','reset','image'].includes(el.type)) {
        const lbl = label(el);
        // Ancestor required attributes are diagnostic only: host semantics vary.
        const hosts = ancestry(el).filter(n => n.shadowRoot);
        controls.push({
          scope, observation_key: `${scope}/${local}`,
          tag: el.tagName.toLowerCase(), type: el.type || el.tagName.toLowerCase(),
          id: el.id || '', name: el.getAttribute('name') || '', label: lbl,
          role: el.getAttribute('role') || '', visible: shown(el), disabled: !!el.disabled,
          required: !!el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(lbl),
          host_required_hint: hosts.some(n => n.hasAttribute('required') || n.getAttribute('aria-required') === 'true'),
          accept: el.getAttribute('accept') || '', multiple: !!el.multiple
        });
      }
      if (el.matches('button,input[type="submit"],input[type="button"],a[role="button"]')) {
        actions.push({scope, observation_key: `${scope}/${local}`,
          label: norm(el.textContent || el.getAttribute('aria-label')),
          type: el.getAttribute('type') || '', visible: shown(el), disabled: !!el.disabled});
      }
      if (el.shadowRoot) visit(el.shadowRoot, `${scope}/${local}::shadow`);
    }
  };
  visit(document, 'document');
  return {controls, actions};
}
"""


def inspect_shadow_form(page) -> dict:
    """One DOM read; no navigation, fill, upload, click or form-value capture."""
    return summarize_shadow_form(page.evaluate(SHADOW_FORM_EXTRACTOR))


def summarize_shadow_form(observation: dict) -> dict:
    # Allowlist output fields: never retain native input values, files, HTML,
    # arbitrary browser state, or candidate data supplied by a caller.
    keys = ("scope", "observation_key", "tag", "type", "id", "name", "label", "role",
            "visible", "disabled", "required", "host_required_hint", "accept", "multiple")
    controls = [{k: c[k] for k in keys if k in c} for c in observation["controls"]]
    actions = [{k: a[k] for k in ("scope", "observation_key", "label", "type", "visible", "disabled") if k in a}
               for a in observation["actions"]]
    identities = [c["observation_key"] for c in controls]
    if len(identities) != len(set(identities)):
        raise ValueError("DUPLICATE_OBSERVATION_IDENTITY")
    locator_groups = {}
    for c in controls:
        kind = "id" if c.get("id") else "name"
        value = c.get(kind)
        if value:
            locator_groups.setdefault((c["tag"], kind, value), []).append(c["observation_key"])
    collisions = [group for group in locator_groups.values() if len(group) > 1]
    payload = {"controls": controls, "actions": actions}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"adapter_version": "smartrecruiters-shadow-inspection-v1", **payload,
            "schema_fingerprint": fingerprint, "unscoped_locator_collisions": collisions,
            "file_control_keys": [c["observation_key"] for c in controls if c.get("type") == "file"],
            "next_observed": any(a.get("visible") and a.get("label", "").strip().lower() in {"next", "continue", "proceed"} for a in actions),
            "inspection_only": True, "live_execution_ready": False,
            "form_value_write_attempts": 0, "file_upload_attempts": 0, "submit_attempts": 0}

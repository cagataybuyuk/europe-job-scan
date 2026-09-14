"""Read-only compatibility check for the current single-page executor.

This is not a readiness/approval check. Unsupported structures must be handled
by a scoped adapter before any personal data is written or uploaded.
"""

FORM_COMPATIBILITY_PROBE = r"""
() => {
  const controls = [];
  let shadowControls = 0;
  let nextActions = 0;
  const visit = (root, inShadow) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.matches('input,textarea,select') &&
          !['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type)) {
        controls.push(el);
        if (inShadow) shadowControls++;
      }
      if (el.matches('button,input[type="button"],input[type="submit"],a[role="button"]')) {
        const label = (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
        const style = window.getComputedStyle(el);
        if (/^(next|continue|proceed)(\s|$)/i.test(label) &&
            style.display !== 'none' && style.visibility !== 'hidden' &&
            el.getClientRects().length > 0) nextActions++;
      }
      if (el.shadowRoot) visit(el.shadowRoot, true);
    }
  };
  visit(document, false);
  const counts = new Map();
  for (const el of controls) {
    const key = el.id ? `${el.tagName}:id:${el.id}` :
      el.name ? `${el.tagName}:name:${el.name}` : '';
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  }
  return {
    control_count: controls.length,
    shadow_control_count: shadowControls,
    duplicate_locator_count: Array.from(counts.values()).filter(n => n > 1).length,
    next_action_count: nextActions
  };
}
"""


def compatibility_blockers(observed: dict) -> tuple[str, ...]:
    blockers = []
    if not observed.get("control_count"):
        blockers.append("FORM_CONTROLS_NOT_READY")
    if observed.get("shadow_control_count"):
        blockers.append("SHADOW_DOM_ADAPTER_REQUIRED")
    if observed.get("duplicate_locator_count"):
        blockers.append("AMBIGUOUS_CONTROL_LOCATOR")
    if observed.get("next_action_count"):
        blockers.append("MULTISTEP_ADAPTER_REQUIRED")
    return tuple(blockers)

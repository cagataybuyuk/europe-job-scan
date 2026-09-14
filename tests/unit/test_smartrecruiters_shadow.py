from copy import deepcopy
import importlib.util
import json
import unittest

from ejs.services.smartrecruiters_shadow import inspect_shadow_form, summarize_shadow_form


class ShadowSummaryTests(unittest.TestCase):
    def sample(self):
        return {"controls": [
            {"scope": "document/a::shadow", "observation_key": "document/a::shadow/input@0", "tag": "input", "type": "file", "id": "file-input"},
            {"scope": "document/b::shadow", "observation_key": "document/b::shadow/input@0", "tag": "input", "type": "file", "id": "file-input"},
        ], "actions": []}

    def test_file_collision_is_reported_without_selecting_an_upload_target(self):
        result = summarize_shadow_form(self.sample())
        self.assertEqual(len(result["file_control_keys"]), 2)
        self.assertEqual(result["unscoped_locator_collisions"], [result["file_control_keys"]])
        self.assertFalse(result["live_execution_ready"])

    def test_values_and_files_are_not_retained_or_fingerprinted(self):
        observed = self.sample()
        original = deepcopy(observed)
        for c in observed["controls"]:
            c.update(value="PRIVATE_VALUE_SENTINEL", files=["PRIVATE_FILE_SENTINEL"])
        result = summarize_shadow_form(observed)
        self.assertEqual(result["schema_fingerprint"], summarize_shadow_form(original)["schema_fingerprint"])
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_duplicate_identity_is_rejected(self):
        observed = self.sample()
        observed["controls"][1]["observation_key"] = observed["controls"][0]["observation_key"]
        with self.assertRaisesRegex(ValueError, "DUPLICATE_OBSERVATION_IDENTITY"):
            summarize_shadow_form(observed)

    def test_single_evaluation_and_no_mutation_methods(self):
        sample = self.sample()
        class ReadOnlyPage:
            calls = 0
            def evaluate(self, script):
                self.calls += 1
                return sample
        page = ReadOnlyPage()
        result = inspect_shadow_form(page)
        self.assertEqual(page.calls, 1)
        self.assertEqual([result[k] for k in ("form_value_write_attempts", "file_upload_attempts", "submit_attempts")], [0, 0, 0])


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class ShadowBrowserTests(unittest.TestCase):
    def test_nested_shadow_scopes_labels_and_read_only_invariants(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content('''<section id="auto"></section><section id="resume"></section>
                    <script>
                    window.mutations = 0;
                    const a = document.getElementById('auto').attachShadow({mode:'open'});
                    a.innerHTML = '<label for="file-input">Autocomplete profile</label><input type="file" id="file-input">';
                    const r = document.getElementById('resume').attachShadow({mode:'open'});
                    r.innerHTML = '<label for="file-input">Resume</label><input type="file" id="file-input"><div id="nested" required></div><button type="button">Next</button>';
                    const n = r.getElementById('nested').attachShadow({mode:'open'});
                    n.innerHTML = '<label for="first">First name*</label><input id="first" value="PRIVATE_SENTINEL"><input aria-label="Disabled" disabled><input aria-label="Invisible" style="display:none">';
                    for(const root of [document,a,r,n]) for(const event of ['input','change','click','submit']) root.addEventListener(event,()=>window.mutations++);
                    </script>''')
                result = inspect_shadow_form(page)
                self.assertEqual(len(result["controls"]), 5)
                self.assertEqual(len(result["file_control_keys"]), 2)
                self.assertEqual(len(result["unscoped_locator_collisions"]), 1)
                by_label = {c["label"]: c for c in result["controls"]}
                self.assertNotEqual(by_label["Resume"]["scope"], by_label["Autocomplete profile"]["scope"])
                self.assertTrue(by_label["First name*"]["required"])
                self.assertTrue(by_label["First name*"]["host_required_hint"])
                self.assertTrue(by_label["Disabled"]["disabled"])
                self.assertFalse(by_label["Invisible"]["visible"])
                self.assertTrue(result["next_observed"])
                self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
                self.assertEqual(page.locator('#first').input_value(), 'PRIVATE_SENTINEL')
                self.assertEqual(page.evaluate('window.mutations'), 0)
                self.assertEqual(result["schema_fingerprint"], inspect_shadow_form(page)["schema_fingerprint"])
                self.assertEqual(page.locator('input[type=file]').evaluate_all('(els)=>els.map(e=>e.files.length)'), [0,0])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()

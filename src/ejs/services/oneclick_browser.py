"""Playwright implementation of the reviewed one-application driver.

Own a fresh browser context; never import the user's browser cookies or accounts.
All network traffic goes through the exact reviewed boundary. Raw answers, request
bodies, response bodies, OAuth content and CV data are never logged or persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

from ejs.services.oneclick_target import OneClickNetworkBoundary
from ejs.services.single_application_runtime import ConfirmationObservation, UploadObservation, digest, require


# Static observer code, never supplied by a model, employer or manifest. Traverse
# open shadow roots; fields in opaque frames/closed components remain a blocker.
_DOM_HELPERS = r"""
 const labelOf = node => {
   const root=node.getRootNode();
   const ids=(node.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
   return (node.getAttribute('aria-label') ||
     (node.labels ? Array.from(node.labels).map(x=>x.textContent).join(' ') : '') ||
     ids.map(id=>root.getElementById?.(id)?.textContent||'').join(' ') ||
     node.getAttribute('placeholder') ||
     (node.getAttribute('role')==='radio'?node.textContent:'') || '').replace(/\s+/g,' ').trim();
 };
 const descendants = node => {
   const out=[];
   const walk = root => {for(const child of root.children||[]){out.push(child);walk(child);if(child.shadowRoot)walk(child.shadowRoot);}};
   walk(node);if(node.shadowRoot)walk(node.shadowRoot);return out;
 };
 const radiosOf = node => descendants(node).filter(child =>
   child.type==='radio'||(child.getAttribute('role')==='radio'&&!descendants(child).some(x=>x.type==='radio')));
"""
_DESCRIBE = "el => {" + _DOM_HELPERS + r"""
 const role=el.getAttribute('role');
 const kind=el.tagName==='SELECT'||role==='combobox'?'select':role==='radiogroup'?'radio':el.tagName==='TEXTAREA'?'text':
   el.tagName==='INPUT'?el.type:({combobox:'select',textbox:'text'}[role]||role||'unknown');
 const radios=role==='radiogroup'?radiosOf(el):[];
 return {tag:el.tagName.toLowerCase(),kind,label:labelOf(el),name:el.getAttribute('name')||'',
   required:el.required===true||el.hasAttribute('required')||el.getAttribute('aria-required')==='true'||radios.some(x=>x.required===true||x.getAttribute('aria-required')==='true'),
   disabled:el.disabled===true||el.getAttribute('aria-disabled')==='true',
   options:el.tagName==='SELECT'?Array.from(el.options).map(x=>x.textContent.trim()):radios.map(labelOf)};
}"""
_DEDUPLICATED_SPL_WRAPPER = "el => {" + _DOM_HELPERS + r"""
 const tag=el.tagName;
 if(!['SPL-NUMBER-FIELD','SPL-INPUT','SPL-AUTOCOMPLETE','SPL-CHECKBOX','SPL-RADIO-GROUP'].includes(tag)||!el.id)return false;
 const semantic=descendants(el).filter(node => {
   if(node.tagName==='INPUT')return !['hidden','button','submit','reset'].includes(node.type);
   return ['SELECT','TEXTAREA'].includes(node.tagName)||['radio','radiogroup','checkbox','combobox','textbox'].includes(node.getAttribute('role'));
 });
 let owned=semantic;
 if(tag==='SPL-RADIO-GROUP'){
   // The reviewed SPL group owns one anonymous FIELDSET, whose radio options
   // are separate SPL shadow components. No other semantic control is hidden.
   owned=semantic.filter(node=>node.type!=='radio'&&node.getAttribute('role')!=='radio');
 }
 if(owned.length!==1)return false;
 const control=owned[0];
 if(!(control.required===true||control.getAttribute('aria-required')==='true'))return false;
 if(tag==='SPL-RADIO-GROUP')return control.tagName==='FIELDSET'&&control.getAttribute('role')==='radiogroup'&&(!control.id||control.id===el.id);
 if(control.tagName!=='INPUT'||control.id!==el.id)return false;
 if(tag==='SPL-NUMBER-FIELD')return control.type==='number';
 if(tag==='SPL-AUTOCOMPLETE')return control.type==='text'&&control.getAttribute('role')==='combobox';
 if(tag==='SPL-CHECKBOX')return control.type==='checkbox';
 return ['text','email','tel','number','search'].includes(control.type);
}"""
_VALUE = "el => {" + _DOM_HELPERS + """
 if(el.getAttribute('role')==='radiogroup'){
   const checked=radiosOf(el).filter(x=>x.type==='radio'?x.checked:x.getAttribute('aria-checked')==='true');
   return checked.length===1?labelOf(checked[0]):checked.length?'AMBIGUOUS_RADIO_SELECTION':'';
 }
 if(el.getAttribute('role')==='combobox'){
   const ariaValue=el.getAttribute('aria-valuetext');
   return ariaValue!==null?ariaValue:(typeof el.value==='string'?el.value:'');
 }
 if(el.type==='checkbox'||el.type==='radio')return el.checked;
 if(['radio','checkbox'].includes(el.getAttribute('role')))return el.getAttribute('aria-checked')==='true';
 if(el.tagName==='SELECT')return el.selectedOptions?.[0]?.textContent.trim()||'';
 return el.value||'';
}"""
_RETURN_DOM = "el => {" + _DOM_HELPERS + """
 return [el,...descendants(el)].map(node=>({tag:node.tagName,shadow:!!node.shadowRoot,
   children:node.children.length,attributes:Array.from(node.attributes).filter(x=>
     ['id','name','role','type','class','style','hidden','required','disabled','checked','selected',
      'autocomplete','for','tabindex','src','href','action','formaction'].includes(x.name)||x.name.startsWith('aria-')
   ).map(x=>[x.name,x.value]).sort()}));
}"""


def _widget_url(url):
    try:
        require(type(url) is str and len(url) <= 4096 and url.isascii()
                and not any(ord(c) < 33 or ord(c) == 127 for c in url) and '\\' not in url)
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and parsed.netloc in ('linkedin.com', 'www.linkedin.com')
                and parsed.path == '/talentwidgets/apply-with-linkedin' and not parsed.query)
        # Actual reviewed widget metadata uses these two public fragment keys,
        # not a query/redirect/OAuth permission URL. No other keys are accepted.
        pairs = parse_qsl(parsed.fragment, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        require(len(pairs) == len({key for key, _ in pairs})
                and all(re.fullmatch(r'[A-Za-z0-9_]{1,64}', key) and len(value) <= 2048
                        and not any(ord(c) < 32 or ord(c) == 127 for c in value) for key, value in pairs))
        values = dict(pairs)
        require(set(values) == {'xdOrigin', 'xdChannel'}
                and values['xdOrigin'] == 'https://jobs.smartrecruiters.com'
                and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', values['xdChannel']))
        return True
    except Exception:
        return False


class OneClickBrowser:
    def __init__(self, context, boundary: OneClickNetworkBoundary, manifest: dict):
        self.context, self.boundary, self.manifest = context, boundary, manifest
        self.page = context.new_page()
        self.page.set_default_timeout(10_000)
        self._step = 0
        self._external_surface = False
        self._submit_called = False
        self._upload = None
        self._popup = False
        self._response = None
        self._owned_page, self._owned_context, self._owned_boundary = self.page, context, boundary
        self._owned_target = boundary.target
        self._entry_handoff_pending = self._entry_observation_issued = self._returned_entry_reviewed = False
        self._entry_return_ready = self._entry_return_consumed = self._returned_widget_allowed = False
        self._entry_observation = self._accepted_entry_observation = self._handoff_widget = None
        self._handoff_widget_src = None
        self._entry_effect_attempts = self._popup_generation = self._document_generation = 0
        self._entry_network_fault = False
        context.route('**/*', self._route)
        # Playwright >=1.48 is required for this lane (the legacy project minimum
        # remains unchanged). Do not silently continue if WebSocket fencing is absent.
        require(callable(getattr(context, 'route_web_socket', None)), 'APPLICATION_WEBSOCKET_FENCE_REQUIRED')
        # A routed socket stays disconnected unless connect_to_server() is
        # explicitly called. Synchronous close here can deadlock Chromium.
        context.route_web_socket('**/*', self._deny_socket)
        context.on('page', self._new_page)
        self.page.on('response', self._observe_response)
        self.page.on('framenavigated', self._entry_navigation)

    def _entry_navigation(self, frame):
        if frame == self._owned_page.main_frame:
            self._document_generation += 1

    def _new_page(self, page):
        if page != self.page:
            self._popup = True
            self._popup_generation += 1
            # A new OAuth/CAPTCHA/permission window is a human handoff. It is
            # never inspected, granted permissions or populated by this driver.

    def _deny_socket(self, socket):
        self._external_surface = True

    def _route(self, route):
        request = route.request
        if self.boundary.allows(request.method, request.url, request.resource_type, request.is_navigation_request()):
            route.fallback()
        else:
            self._entry_network_fault = True
            route.abort('blockedbyclient')

    def _observe_response(self, response):
        if response.request.method == 'POST' and response.url == self.boundary.target.application_url:
            self._response = response

    def open(self, url):
        require(url == self.boundary.target.entry_url)
        self.page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        self.page.locator('input, select, textarea, [role="radio"], [role="radiogroup"], '
                          '[role="combobox"], [role="checkbox"], [role="textbox"]').first.wait_for(state='attached', timeout=30_000)

    def preferred_entry(self):
        if self._entry_return_ready:
            self._entry_return_ready = False
            self._entry_return_consumed = True
            observation = self._accepted_entry_observation
            require(0 <= monotonic() - observation['observed_at'] <= 120, 'APPLICATION_ENTRY_RETURN_REJECTED')
            require(self._entry_snapshot()['proof'] == observation['proof'], 'APPLICATION_ENTRY_RETURN_REJECTED')
            return
        require(not self._entry_return_consumed, 'APPLICATION_ENTRY_RETURN_CONSUMED')
        # Exact visible action only, never an Easy Apply/profile/share link.
        action = self.page.get_by_role('button', name=re.compile(r'^Apply with LinkedIn$', re.I))
        link = self.page.get_by_role('link', name=re.compile(r'^Apply with LinkedIn$', re.I))
        visible = self._visible(action) + self._visible(link)
        widgets = []
        for iframe in self.page.locator('iframe').all():
            require(iframe.is_visible() and iframe.get_attribute('srcdoc') is None
                    and _widget_url(iframe.get_attribute('src')), 'APPLICATION_ENTRY_SURFACE_REVIEW_REQUIRED')
            widgets.append(iframe)
        visible += widgets
        require(len(visible) <= 1, 'APPLICATION_ENTRY_AMBIGUOUS')
        if visible:
            # The first MVP's reviewed network lane deliberately has no OAuth
            # mutation authority. Return to a separately reviewed retained entry
            # after the actual human grant; no blind native retry is permitted.
            self._entry_handoff_pending = True
            if widgets:
                self._handoff_widget = widgets[0].element_handle()
                self._handoff_widget_src = widgets[0].get_attribute('src')
            raise PermissionError('APPLICATION_LINKEDIN_HUMAN_HANDOFF_REQUIRED')

    def _returned_frames_clear(self):
        for iframe in self.page.locator('iframe').all():
            if (not self._returned_widget_allowed or self._handoff_widget is None or not iframe.is_visible()
                    or iframe.get_attribute('srcdoc') is not None or iframe.get_attribute('src') != self._handoff_widget_src
                    or not _widget_url(iframe.get_attribute('src'))
                    or not iframe.evaluate('(el, owned) => el===owned', self._handoff_widget)):
                return False
            frame = iframe.element_handle().content_frame()
            if frame is None or frame.url != self._handoff_widget_src:
                return False
        return True

    def _entry_snapshot(self):
        require(self.page is self._owned_page and self.context is self._owned_context
                and self.boundary is self._owned_boundary and self.boundary.target is self._owned_target
                and self.page.context is self.context
                and not self.page.is_closed() and self.page.url == self.boundary.target.entry_url
                and self._step == 0 and self._entry_effect_attempts == 0 and not self._submit_called
                and self._upload is None and self.boundary.phase == 'read'
                and self.boundary.application_attempts == self.boundary.application_requests_allowed == self.boundary.upload_requests_allowed == 0
                and self.boundary.blocked_write_count == 0 and not self._entry_network_fault and not self._external_surface
                and self.context.pages == [self.page], 'APPLICATION_ENTRY_RETURN_REJECTED')
        # Check auth/challenge signals before reading or hashing field values.
        body = self.page.locator('body').inner_text()
        require(not re.search(r'verification code|one.time.code|passcode|security code|captcha|verify you are human|'
                              r'access denied|sign in to continue|log in to continue|two.factor|authentication|verify your email',
                              body, re.I), 'APPLICATION_ENTRY_RETURN_REJECTED')
        controls = self._controls()
        required_known = []
        step = self.manifest['steps'][0]
        for binding in step['fields'] + ([step['upload']] if 'upload' in step else []):
            required_known.append(self._locator(binding))
        for locator, info in controls:
            signals = ' '.join(str(locator.get_attribute(attr) or '') for attr in ('type', 'autocomplete', 'name', 'id')) + ' ' + info['label']
            require(info['kind'] in ('text', 'email', 'tel', 'number', 'select', 'checkbox', 'radio', 'file')
                    and not re.search(r'password|passcode|one.time.code|\botp\b|\bpin\b|security.code|verification.code|email.code|sms.code|captcha', signals, re.I)
                    and info['label'].casefold().strip() not in ('code', 'pin', 'otp')
                    and (not info['required'] or any(locator.evaluate('(el, known) => el===known', known.element_handle())
                                                    for known in required_known)), 'APPLICATION_ENTRY_RETURN_REJECTED')
        frames = self.page.locator('iframe').all()
        if frames:
            require(len(frames) == 1 and self._handoff_widget is not None, 'APPLICATION_ENTRY_RETURN_REJECTED')
            iframe = frames[0]
            require(iframe.is_visible() and iframe.get_attribute('srcdoc') is None
                    and iframe.get_attribute('src') == self._handoff_widget_src and _widget_url(self._handoff_widget_src)
                    and iframe.evaluate('(el, owned) => el===owned', self._handoff_widget), 'APPLICATION_ENTRY_RETURN_REJECTED')
            frame = iframe.element_handle().content_frame()
            require(frame is not None and frame.url == self._handoff_widget_src, 'APPLICATION_ENTRY_RETURN_REJECTED')
        shape = sorted([info for _, info in controls], key=lambda x: json.dumps(x, sort_keys=True))
        proof = digest({'dom': self.page.locator('body').evaluate(_RETURN_DOM),
                        'body': hashlib.sha256(body.encode()).hexdigest(),
                        'controls': [(info, locator.evaluate(_VALUE)) for locator, info in controls],
                        'document': self._document_generation, 'popups': self._popup_generation})
        return {'proof': proof, 'surface_fingerprint': digest(shape), 'widget_present': bool(frames)}

    def observe_entry_return(self):
        """Observe only the retained owned entry; never export values or credentials."""
        require(self._entry_handoff_pending and not self._entry_observation_issued, 'APPLICATION_ENTRY_RETURN_REJECTED')
        snapshot = self._entry_snapshot()
        observation = {**snapshot, 'observation_id': secrets.token_hex(24),
                       'observed_at': monotonic(), 'observed_utc': datetime.now(timezone.utc)}
        self._entry_observation, self._entry_observation_issued = observation, True
        return {'observation_id': observation['observation_id'],
                'surface_fingerprint': observation['surface_fingerprint'],
                'observed_at': observation['observed_utc'].isoformat()}

    def accept_reviewed_entry_return(self, observation_id, manifest):
        """Consume once after the caller loads externally pinned fresh facts/review.

        The independent reviewer must inspect the actual values/profile before
        externally pinning this nonce-bound manifest; a structural fingerprint
        is not candidate verification. This API does not perform human OAuth,
        verify the external pin itself or grant network/credential permissions.
        """
        observation, self._entry_observation = self._entry_observation, None
        require(observation is not None and self._entry_handoff_pending, 'APPLICATION_ENTRY_RETURN_REJECTED')
        try:
            require(type(observation_id) is str and secrets.compare_digest(observation_id, observation['observation_id'])
                    and 0 <= monotonic() - observation['observed_at'] <= 120)
            review = manifest['entry_return_review']
            require(type(review) is dict and set(review) == {'observation_id', 'surface_fingerprint', 'reviewed_at', 'reviewer_ref', 'return_url'}
                    and review['observation_id'] == observation_id
                    and review['surface_fingerprint'] == observation['surface_fingerprint']
                    and review['return_url'] == self.boundary.target.entry_url
                    and review['reviewer_ref'] == manifest['reviewer_ref']
                    and type(review['reviewer_ref']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', review['reviewer_ref']))
            reviewed = datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
            require(reviewed.tzinfo is not None and reviewed.utcoffset().total_seconds() == 0
                    and observation['observed_utc'] <= reviewed <= datetime.now(timezone.utc)
                    and (datetime.now(timezone.utc) - reviewed).total_seconds() <= 120)
            current = self._entry_snapshot()
            require(current['proof'] == observation['proof']
                    and manifest['steps'][0]['url'] == self.boundary.target.entry_url
                    and manifest['steps'][0]['fingerprint'] == observation['surface_fingerprint'])
            old_manifest = self.manifest
            self.manifest = manifest
            self._returned_widget_allowed = observation['widget_present']
            self._returned_entry_reviewed = True
            historical_popup = self._popup
            self._popup = False  # Only now, after observing all extra pages closed.
            try:
                actual = self.inspect()
                expected = {binding['key'] for binding in manifest['steps'][0]['fields'] if binding['required']}
                if 'upload' in manifest['steps'][0]:
                    expected.add(manifest['steps'][0]['upload']['key'])
                require(actual['fingerprint'] == observation['surface_fingerprint'] and actual['challenge'] is False
                        and actual['unexpected_write'] is False and actual['unreviewed_prefill'] is False
                        and actual['required_keys'] == expected)
            except Exception:
                self.manifest, self._popup = old_manifest, historical_popup
                self._returned_entry_reviewed = self._returned_widget_allowed = False
                raise
            self._accepted_entry_observation = observation
            self._entry_return_ready = True
        except Exception:
            raise PermissionError('APPLICATION_ENTRY_RETURN_REJECTED') from None

    def _locator(self, binding):
        locator = self.page.locator(binding['selector'])
        require(locator.count() == 1 and (binding.get('kind') == 'file' or locator.is_visible()), 'APPLICATION_CONTROL_CHANGED')
        return locator

    def _controls(self):
        found = []
        for locator in self.page.locator('input,select,textarea,[role="radio"],[role="radiogroup"],[role="checkbox"],'
                                         '[role="combobox"],[role="textbox"],[aria-required="true"],[required]').all():
            info = locator.evaluate(_DESCRIBE)
            # Only these reviewed SPL hosts may represent the same logical
            # required control as one exact owned native/semantic descendant.
            # Unknown, mismatched or ambiguous wrappers remain blockers.
            if locator.evaluate(_DEDUPLICATED_SPL_WRAPPER):
                continue
            if info['kind'] in ('hidden', 'submit', 'button', 'reset'):
                continue
            if info['kind'] != 'file' and not locator.is_visible():
                # display:contents hosts have no box even when their logical
                # required descendant is visible. An unreviewed wrapper must
                # not disappear merely because its host has no own box.
                owned_visible = (info['required'] and info['kind'] == 'unknown'
                    and '-' in info['tag'] and any(child.is_visible() for child in locator.locator(
                        'input,select,textarea,[role="radio"],[role="radiogroup"],[role="checkbox"],'
                        '[role="combobox"],[role="textbox"]').all()))
                if not owned_visible:
                    continue
            # A reviewed group owns its radio options, including options inside
            # open shadow roots. Other required controls remain independent.
            if locator.evaluate("""el => {
              if(el.type!=='radio'&&el.getAttribute('role')!=='radio')return false;
              for(let node=el.parentElement||el.getRootNode().host;node;node=node.parentElement||node.getRootNode().host){
                if(node.getAttribute('role')==='radiogroup')return true;
              }return false;
            }"""):
                continue
            # Do not count a custom radio wrapper twice if it owns a native input.
            if (info['tag'] != 'input' and info['kind'] == 'radio'
                    and locator.get_attribute('role') != 'radiogroup'
                    and locator.locator('input[type="radio"]').count()):
                continue
            found.append((locator, info))
        return found

    def inspect(self):
        step = self.manifest['steps'][self._step]
        controls = self._controls()
        required_keys, covered = set(), set()
        unreviewed_prefill = False
        for binding in step['fields'] + ([step['upload']] if 'upload' in step else []):
            locator = self._locator(binding)
            info = locator.evaluate(_DESCRIBE)
            require(info['kind'] == binding['kind'] and info['label'] == binding['label']
                    and info['required'] is binding['required'] and not info['disabled'], 'APPLICATION_CONTROL_CHANGED')
            if info['required'] or binding is step.get('upload'):
                required_keys.add(binding['key'])
            for index, (observed, _) in enumerate(controls):
                if observed.evaluate('(el, other) => el===other', locator.element_handle()):
                    covered.add(index)
        for index, (locator, info) in enumerate(controls):
            if index not in covered:
                if info['required']:
                    required_keys.add('unknown:' + digest(info))
                if info['kind'] != 'file' and locator.evaluate(_VALUE) not in ('', False):
                    unreviewed_prefill = True
        # An exact structural fingerprint includes every visible control,
        # including optional fields/options; it contains no entered values/IDs.
        shape = sorted([info for _, info in controls], key=lambda x: json.dumps(x, sort_keys=True))
        body = self.page.locator('body').inner_text().casefold()
        challenge = self._popup or (not self._returned_frames_clear() if self._returned_entry_reviewed
                                   else bool(self.page.locator('iframe').count())) or any(
            text in body for text in ('verify you are human', 'complete the captcha', 'verification code', 'access denied'))
        form_valid = self.page.locator('input,select,textarea').evaluate_all(
            "els => els.filter(x=>x.type!=='hidden'&&!x.disabled).every(x=>x.checkValidity())")
        submit = step.get('submit')
        submit_visible = bool(submit and self.page.locator(submit['selector']).count() == 1
                              and self.page.locator(submit['selector']).is_visible()
                              and self.page.locator(submit['selector']).is_enabled())
        # A reviewed profile card must have a fact-backed visible assertion.
        # Imported LinkedIn cards are never promoted to truth by matching a URL.
        assertions = step.get('profile_assertions', [])
        for assertion in assertions:
            require(self.page.locator(assertion['selector']).count() == 1
                    and self.page.locator(assertion['selector']).inner_text().strip() == assertion['value'],
                    'APPLICATION_IMPORTED_PROFILE_MISMATCH')
        return {'url': self.page.url, 'fingerprint': digest(shape), 'required_keys': required_keys,
                'challenge': challenge, 'unreviewed_prefill': unreviewed_prefill,
                'unexpected_write': self.boundary.blocked_write_count != 0 or self._external_surface,
                'form_valid': form_valid, 'submit_visible': submit_visible}

    def write(self, binding):
        locator = self._locator(binding)
        kind, value = binding['kind'], binding['value']
        if self.readback(binding) == value:
            return
        self._entry_effect_attempts += 1
        if kind == 'select':
            if locator.evaluate("el => el.tagName==='SELECT'"):
                locator.select_option(label=value)
            else:
                require(locator.get_attribute('role') == 'combobox', 'APPLICATION_SELECT_UNREVIEWED')
                locator.click()
                associated = locator.get_attribute('aria-controls')
                if associated:
                    require(bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', associated)), 'APPLICATION_SELECT_MENU_UNREVIEWED')
                    container = self.page.locator(f'[id="{associated}"]')
                    require(container.count() == 1 and container.is_visible()
                            and container.get_attribute('role') in ('listbox', 'menu'), 'APPLICATION_SELECT_MENU_UNREVIEWED')
                    containers = [container]
                else:
                    containers = self._visible(self.page.locator('[role="listbox"],[role="menu"]'))
                options = []
                for container in containers:
                    for option in self._visible(container.get_by_role('option', name=value, exact=True)):
                        self._append_unique(options, option)
                require(len(options) <= 1, 'APPLICATION_SELECT_OPTION_AMBIGUOUS')
                if not options:
                    spl_rows = []
                    for container in containers:
                        for row in container.locator('spl-select-option').all():
                            self._append_unique(spl_rows, row)
                    if spl_rows:
                        # Reviewed SPL rows have no option role. Their children
                        # may be display:contents and duplicate hidden tooltips;
                        # select the uniquely visible exact-text host itself.
                        require(bool(associated), 'APPLICATION_SELECT_MENU_UNREVIEWED')
                        options = [row for row in spl_rows if row.is_visible()
                                   and row.text_content().strip() == value]
                        require(len(options) == 1, 'APPLICATION_SELECT_OPTION_UNAVAILABLE')
                if not options:
                    # Some reviewed widgets expose text in a listbox/menu
                    # without option roles. Never fall back to arbitrary page
                    # text or pick the first of multiple visible matches.
                    for container in containers:
                        for option in self._visible(container.get_by_text(value, exact=True)):
                            self._append_unique(options, option)
                require(len(options) == 1, 'APPLICATION_SELECT_OPTION_UNAVAILABLE')
                options[0].click()
        elif kind == 'checkbox':
            locator.set_checked(value)
        elif kind == 'radio':
            require(value in ('Yes', 'No'), 'APPLICATION_RADIO_VALUE_UNREVIEWED')
            if locator.get_attribute('role') == 'radiogroup':
                radios = self._visible(locator.get_by_role('radio', name=value, exact=True))
                require(len(radios) == 1, 'APPLICATION_RADIO_OPTION_AMBIGUOUS')
                radios[0].check()
            else:
                locator.check()
        else:
            locator.fill(str(value))
            locator.blur()

    def readback(self, binding):
        locator = self._locator(binding)
        value = locator.evaluate(_VALUE)
        if binding['kind'] == 'radio':
            if locator.get_attribute('role') == 'radiogroup':
                require(value != 'AMBIGUOUS_RADIO_SELECTION', 'APPLICATION_RADIO_SELECTION_AMBIGUOUS')
                return value
            return binding['value'] if value is True else ''
        if binding['kind'] == 'number' and type(binding['value']) in (int, float):
            try:
                return type(binding['value'])(value)
            except (ValueError, TypeError):
                return None
        return value

    @staticmethod
    def _visible(locator):
        return [item for item in locator.all() if item.is_visible()]

    @staticmethod
    def _append_unique(items, candidate):
        if not any(candidate.evaluate('(el, other) => el===other', prior.element_handle()) for prior in items):
            items.append(candidate)

    def upload(self, binding, payload, filename):
        require(self._upload is None and self.boundary.blocked_write_count == 0)
        locator = self._locator(binding)
        require(locator.evaluate('(el)=>el.type') == 'file')
        endpoint = self.boundary.target.resume_upload_url(filename)
        self._entry_effect_attempts += 1
        self.boundary.begin_upload()
        try:
            with self.page.expect_response(lambda r: r.url == endpoint and r.request.method == 'POST', timeout=30_000) as pending:
                locator.set_input_files({'name': filename, 'mimeType': 'application/pdf', 'buffer': payload})
            response = pending.value
            require(response.status in (200, 201) and response.request.redirected_from is None)
            data = response.json()
            # Explicit vendor attachment metadata, not a synthetic readback SHA.
            require(isinstance(data, dict) and isinstance(data.get('fileId'), str) and bool(data['fileId'])
                    and data.get('fileName') == filename, 'APPLICATION_UPLOAD_UNVERIFIED')
            require(self.page.get_by_text(filename, exact=True).count() == 1
                    and self.page.get_by_text(filename, exact=True).is_visible(), 'APPLICATION_UPLOAD_UNVERIFIED')
            self._upload = UploadObservation(hashlib.sha256(payload).hexdigest(), len(payload), filename,
                hashlib.sha256(data['fileId'].encode()).hexdigest(), endpoint, True)
            return self._upload
        finally:
            self.boundary.end_upload()

    def advance(self, binding, expected_url):
        require(self._step + 1 < len(self.manifest['steps'])
                and expected_url == self.manifest['steps'][self._step + 1]['url']
                and self.boundary.blocked_write_count == 0)
        locator = self._locator(binding)
        require(locator.inner_text().strip() == binding['label'] and binding['label'] in ('Next', 'Continue'))
        self._entry_effect_attempts += 1
        locator.click()
        self.page.wait_for_url(expected_url, timeout=15_000)
        self._step += 1

    def submit_once(self, binding):
        require(not self._submit_called and self.boundary.blocked_write_count == 0
                and self.boundary.application_attempts == 0)
        locator = self._locator(binding)
        require(locator.inner_text().strip() == binding['label'] and binding['label'] in ('Submit', 'Submit application', 'Send application'))
        self._submit_called = True
        self._entry_effect_attempts += 1
        self.boundary.begin_submit()
        with self.page.expect_response(lambda r: r.url == self.boundary.target.application_url
                                      and r.request.method == 'POST', timeout=30_000) as pending:
            locator.click()
        response = pending.value
        require(response.request.redirected_from is None)
        payload = response.body()
        require(len(payload) <= 1_000_000)
        data = json.loads(payload)
        # Actual schema is independently reviewed; absent identity is uncertainty.
        success = isinstance(data, dict) and data.get('success') is True
        confirmation = self.manifest['confirmation']
        marker = self.page.locator(confirmation['selector'])
        marker.wait_for(state='visible', timeout=15_000)
        success_url = self.boundary.target.screening_url.replace('/screening?', '/success?')
        visible = marker.inner_text().strip() == confirmation['text'] and self.page.url == success_url
        return ConfirmationObservation(response.url, datetime.now(timezone.utc), response.status,
            hashlib.sha256(payload).hexdigest(), success, visible,
            self._popup or self.boundary.application_attempts != 1 or self.boundary.blocked_write_count != 0)
